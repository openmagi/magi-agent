"""Gate2 sandbox workspace canary chat + delivery-receipt logic.

Pure move out of ``magi_agent/transport/chat.py`` (08-PR1). Contains the gate2
sandbox canary route config + env builder, the canary chat runner (readiness,
digest checks, durable-evidence verification, digest-safe failure chains) and
the gate2/user-visible delivery-receipt payload contracts and recorder.
Behavior is unchanged; ``transport.chat`` re-exports these names for
compatibility.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from magi_agent.evidence.gate2_durable_evidence import (
    Gate2DurableEvidenceStore,
)
from magi_agent.gates.gate2_readiness import gate2_readiness_health_metadata
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.user_visible_model_routing import _SAFE_LABEL_RE
from magi_agent.shadow.gate2_activation_loop_a import (
    Gate2SandboxCanaryRequest,
    Gate2SandboxRootReadiness,
    check_gate2_sandbox_root_readiness,
    run_gate2_sandbox_workspace_canary,
)
from magi_agent.transport.chat_shared import (
    _context_continuity_chat_diagnostic,
    _csv_values,
    _env_bool_default_true,
    _fallback_response,
    _is_sha256_digest,
    _is_true,
    _reason_for_gate_error,
    _sha256_digest,
    _shadow_generation_route_config,
)

_DELIVERY_RECEIPT_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    populate_by_name=True,
    hide_input_in_errors=True,
)


class Gate5BSelectedScopeReceiptPayload(BaseModel):
    model_config = _DELIVERY_RECEIPT_MODEL_CONFIG

    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(alias="selectedOwnerUserIdDigest")
    environment: str

    @model_validator(mode="after")
    def _validate_selected_scope(self) -> "Gate5BSelectedScopeReceiptPayload":
        if not _is_sha256_digest(self.selected_bot_digest):
            raise ValueError("selected bot scope must be a digest")
        if not _is_sha256_digest(self.selected_owner_user_id_digest):
            raise ValueError("selected owner scope must be a digest")
        if not _SAFE_LABEL_RE.match(self.environment):
            raise ValueError("selected scope environment must be public-safe")
        return self


class Gate5BUserVisibleDeliveryReceiptPayload(BaseModel):
    model_config = _DELIVERY_RECEIPT_MODEL_CONFIG

    schema_version: Literal["gate5b.userVisibleDeliveryReceipt.v1"] = Field(
        alias="schemaVersion",
    )
    request_digest: str = Field(alias="requestDigest")
    body_digest: str | None = Field(default=None, alias="bodyDigest")
    route_decision: str | None = Field(default=None, alias="routeDecision")
    gate: str | None = None
    delivery_status: Literal[
        "served_to_client",
        "fallback_served",
        "client_aborted",
        "completed_after_client_timeout",
        "python_error",
        "timeout",
        "blocked",
        "harness_failed",
    ] = Field(alias="deliveryStatus")
    reason: str
    response_authority: Literal["python", "typescript"] = Field(alias="responseAuthority")
    python_attempted: bool = Field(default=False, alias="pythonAttempted")
    python_counter_record_present: bool = Field(
        default=False,
        alias="pythonCounterRecordPresent",
    )
    selected_scope: Gate5BSelectedScopeReceiptPayload | None = Field(
        default=None,
        alias="selectedScope",
    )
    served_at: str | None = Field(default=None, alias="servedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    sse_frame_count: int = Field(default=0, ge=0, alias="sseFrameCount")
    tool_receipt_count: int = Field(default=0, ge=0, alias="toolReceiptCount")
    tool_evidence_status: str | None = Field(default=None, alias="toolEvidenceStatus")
    model_attempt_count: int = Field(default=0, ge=0, alias="modelAttemptCount")
    provider_request_count: int = Field(default=0, ge=0, alias="providerRequestCount")
    expected_model_attempt_count: int | None = Field(
        default=None,
        ge=0,
        alias="expectedModelAttemptCount",
    )
    egress_connect_count: int | None = Field(default=None, ge=0, alias="egressConnectCount")
    egress_tunnel_count: int | None = Field(default=None, ge=0, alias="egressTunnelCount")
    egress_discipline_mode: Literal[
        "strict_single_tunnel",
        "bounded_provider_tunnels",
    ] | None = Field(default=None, alias="egressDisciplineMode")
    egress_evidence_status: Literal[
        "observed_egress_evidence_present",
        "missing_observed_egress_evidence",
    ] | None = Field(default=None, alias="egressEvidenceStatus")
    egress_evidence_source: str | None = Field(default=None, alias="egressEvidenceSource")
    egress_evidence_redaction_status: str | None = Field(
        default=None,
        alias="egressEvidenceRedactionStatus",
    )
    egress_evidence_decision_reason: str | None = Field(
        default=None,
        alias="egressEvidenceDecisionReason",
    )
    model_attempt_digest: str | None = Field(default=None, alias="modelAttemptDigest")
    max_provider_tunnels_per_model_attempt: int | None = Field(
        default=None,
        ge=0,
        alias="maxProviderTunnelsPerModelAttempt",
    )
    egress_host_classes: tuple[str, ...] = Field(default=(), alias="egressHostClasses")
    egress_correlation_digest: str | None = Field(
        default=None,
        alias="egressCorrelationDigest",
    )
    egress_window_started_at: str | None = Field(
        default=None,
        alias="egressWindowStartedAt",
    )
    egress_window_ended_at: str | None = Field(default=None, alias="egressWindowEndedAt")
    egress_outside_gate_window: bool = Field(
        default=False,
        alias="egressOutsideGateWindow",
    )
    workspace_mutation_receipt_digest: str | None = Field(
        default=None,
        alias="workspaceMutationReceiptDigest",
    )
    rollback_receipt_digest: str | None = Field(
        default=None,
        alias="rollbackReceiptDigest",
    )
    sandbox_path_digest: str | None = Field(default=None, alias="sandboxPathDigest")
    source_ledger_digest: str | None = Field(default=None, alias="sourceLedgerDigest")
    final_projection_digest: str | None = Field(
        default=None,
        alias="finalProjectionDigest",
    )
    research_evidence_status: Literal["passed", "missing_evidence", "failed"] | None = (
        Field(default=None, alias="researchEvidenceStatus")
    )
    citation_evidence_status: Literal["passed", "missing_evidence", "failed"] | None = (
        Field(default=None, alias="citationEvidenceStatus")
    )
    verifier_evidence_status: Literal["passed", "missing_evidence", "failed"] | None = (
        Field(default=None, alias="verifierEvidenceStatus")
    )
    final_projection_evidence_status: (
        Literal["passed", "missing_evidence", "failed"] | None
    ) = Field(default=None, alias="finalProjectionEvidenceStatus")
    source_inspected_event_count: int = Field(
        default=0,
        ge=0,
        alias="sourceInspectedEventCount",
    )
    rule_check_event_count: int = Field(default=0, ge=0, alias="ruleCheckEventCount")
    unsupported_claim_omitted_count: int = Field(
        default=0,
        ge=0,
        alias="unsupportedClaimOmittedCount",
    )
    output_digest: str | None = Field(default=None, alias="outputDigest")
    output_leak_status: dict[str, object] | None = Field(
        default=None,
        alias="outputLeakStatus",
    )
    @model_validator(mode="after")
    def _validate_receipt(self) -> "Gate5BUserVisibleDeliveryReceiptPayload":
        if not _is_sha256_digest(self.request_digest):
            raise ValueError("requestDigest must be a digest")
        for digest in (
            self.body_digest,
            self.output_digest,
            self.workspace_mutation_receipt_digest,
            self.rollback_receipt_digest,
            self.sandbox_path_digest,
            self.source_ledger_digest,
            self.final_projection_digest,
        ):
            if digest is not None and not _is_sha256_digest(digest):
                raise ValueError("receipt digest fields must be sha256 digests")
        if self.egress_correlation_digest is not None and not _is_sha256_digest(
            self.egress_correlation_digest
        ):
            raise ValueError("egress correlation must be a digest")
        if self.model_attempt_digest is not None and not _is_sha256_digest(
            self.model_attempt_digest
        ):
            raise ValueError("model attempt digest must be a digest")
        if not _SAFE_LABEL_RE.match(self.reason):
            raise ValueError("reason must be public-safe")
        for label in (
            self.route_decision,
            self.gate,
            self.fallback_reason,
            self.tool_evidence_status,
            self.egress_evidence_status,
            self.egress_evidence_source,
            self.egress_evidence_redaction_status,
            self.egress_evidence_decision_reason,
            self.research_evidence_status,
            self.citation_evidence_status,
            self.verifier_evidence_status,
            self.final_projection_evidence_status,
        ):
            if label is not None and not _SAFE_LABEL_RE.match(label):
                raise ValueError("receipt labels must be public-safe")
        for timestamp in (self.served_at, self.completed_at):
            if timestamp is not None and not re.fullmatch(r"[0-9TZ:.+-]{10,40}", timestamp):
                raise ValueError("receipt timestamps must be public-safe")
        for timestamp in (self.egress_window_started_at, self.egress_window_ended_at):
            if timestamp is not None and not re.fullmatch(r"[0-9TZ:.+-]{10,40}", timestamp):
                raise ValueError("egress window timestamps must be public-safe")
        for host_class in self.egress_host_classes:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", host_class):
                raise ValueError("egress host classes must be sanitized class labels")
        if self.delivery_status == "served_to_client" and self.response_authority != "python":
            raise ValueError("served_to_client receipt requires python authority")
        if self.delivery_status != "served_to_client" and self.response_authority != "typescript":
            raise ValueError("fallback receipt requires typescript authority")
        return self


class Gate1ASelectedAttemptPreflightPayload(BaseModel):
    model_config = _DELIVERY_RECEIPT_MODEL_CONFIG

    schema_version: Literal["gate1a.selectedAttemptPreflightRequest.v1"] = Field(
        alias="schemaVersion",
    )
    request_digest: str = Field(alias="requestDigest")
    fallback_receipt_path_available: bool = Field(alias="fallbackReceiptPathAvailable")
    selected_scope: Gate5BSelectedScopeReceiptPayload | None = Field(
        default=None,
        alias="selectedScope",
    )

    @model_validator(mode="after")
    def _validate_preflight_payload(self) -> "Gate1ASelectedAttemptPreflightPayload":
        if not _is_sha256_digest(self.request_digest):
            raise ValueError("requestDigest must be a digest")
        return self


@dataclass(frozen=True, init=False)
class Gate2SandboxWorkspaceCanaryConfig:
    enabled: bool
    kill_switch_enabled: bool
    selected_bot_digest: str
    selected_owner_user_id_digest: str
    environment: str
    environment_allowlist: tuple[str, ...]
    sandbox_root: Path | None
    selected_mutation_provider_enabled: bool
    durable_evidence_store: Gate2DurableEvidenceStore | None

    def __init__(
        self,
        enabled: object = False,
        kill_switch_enabled: object = True,
        selected_bot_digest: str = "",
        selected_owner_user_id_digest: str = "",
        environment: str = "",
        environment_allowlist: tuple[str, ...] = (),
        sandbox_root: str | Path | None = None,
        selected_mutation_provider_enabled: object = False,
        durable_evidence_store: Gate2DurableEvidenceStore | None = None,
        *,
        killSwitchEnabled: object | None = None,
        selectedBotDigest: str | None = None,
        selectedOwnerUserIdDigest: str | None = None,
        environmentAllowlist: tuple[str, ...] | None = None,
        sandboxRoot: str | Path | None = None,
        selectedMutationProviderEnabled: object | None = None,
        durableEvidenceStore: Gate2DurableEvidenceStore | None = None,
    ) -> None:
        root = sandboxRoot if sandboxRoot is not None else sandbox_root
        object.__setattr__(self, "enabled", enabled is True)
        object.__setattr__(
            self,
            "kill_switch_enabled",
            kill_switch_enabled if killSwitchEnabled is None else killSwitchEnabled,
        )
        object.__setattr__(self, "selected_bot_digest", selectedBotDigest or selected_bot_digest)
        object.__setattr__(
            self,
            "selected_owner_user_id_digest",
            selectedOwnerUserIdDigest or selected_owner_user_id_digest,
        )
        object.__setattr__(self, "environment", environment)
        object.__setattr__(
            self,
            "environment_allowlist",
            tuple(environmentAllowlist or environment_allowlist),
        )
        object.__setattr__(self, "sandbox_root", Path(root) if root else None)
        _selected_provider = (
            selectedMutationProviderEnabled
            if selectedMutationProviderEnabled is not None
            else selected_mutation_provider_enabled
        )
        object.__setattr__(
            self,
            "selected_mutation_provider_enabled",
            _selected_provider is True or _selected_provider == "1",
        )
        _evidence_store = (
            durableEvidenceStore
            if durableEvidenceStore is not None
            else durable_evidence_store
        )
        object.__setattr__(self, "durable_evidence_store", _evidence_store)


def build_gate2_sandbox_workspace_canary_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate2SandboxWorkspaceCanaryConfig:
    del runtime_config
    return Gate2SandboxWorkspaceCanaryConfig(
        enabled=_is_true(env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENABLED")),
        killSwitchEnabled=_env_bool_default_true(
            env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_KILL_SWITCH")
        ),
        selectedBotDigest=env.get(
            "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_BOT_DIGEST",
            "",
        ).strip(),
        selectedOwnerUserIdDigest=env.get(
            "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
            "",
        ).strip(),
        environment=env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENVIRONMENT", "").strip(),
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENV_ALLOWLIST", "")
        ),
        sandboxRoot=env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT"),
        selectedMutationProviderEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED")
        ),
        durableEvidenceStore=_build_gate2_durable_evidence_store(env),
    )


def _build_gate2_durable_evidence_store(
    env: Mapping[str, str],
) -> Gate2DurableEvidenceStore | None:
    """Create a durable evidence store when Gate 2 selected provider is enabled."""
    if not _is_true(env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED")):
        return None
    sandbox_root = env.get("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT")
    if not sandbox_root:
        return None
    evidence_path = Path(sandbox_root) / ".gate2-evidence" / "durable-evidence.json"
    return Gate2DurableEvidenceStore(evidence_path)


def _gate2_sandbox_canary_config(runtime: OpenMagiRuntime) -> Gate2SandboxWorkspaceCanaryConfig:
    config = getattr(runtime, "gate2_sandbox_workspace_canary_config", None)
    if isinstance(config, Gate2SandboxWorkspaceCanaryConfig):
        return config
    return build_gate2_sandbox_workspace_canary_config_from_env(os.environ, runtime.config)


def _gate2_selected_sandbox_root_readiness(
    config: Gate2SandboxWorkspaceCanaryConfig,
) -> Gate2SandboxRootReadiness | None:
    if not config.enabled:
        return None
    if not config.selected_mutation_provider_enabled:
        return None
    return check_gate2_sandbox_root_readiness(config.sandbox_root)


def _verify_gate2_durable_evidence_on_disk(
    store: Gate2DurableEvidenceStore,
    *,
    request_digest: str,
) -> tuple[bool, str | None]:
    """Re-read the durable evidence from disk and confirm all five categories.

    ``record_all_evidence`` returns an in-memory success flag, but the live
    Gate 2 failure mode was an HTTP 200 response with NO file on disk. This
    re-reads the persisted record so the selected path only claims success when
    the file actually exists and contains every required category.
    """
    try:
        if not store.store_path.exists():
            return False, "evidence_file_missing"
    except OSError:
        return False, "evidence_file_unreadable"
    record = store.get_evidence(request_digest)
    if record is None:
        return False, "evidence_record_missing"
    if not record.all_evidence_present:
        missing = ",".join(record.missing_evidence) or "unknown"
        return False, f"evidence_incomplete:{missing}"
    return True, None


def _safe_gate2_chain_label(value: object) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    return label if _SAFE_LABEL_RE.match(label) else "redacted"


def _gate2_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _gate2_scope_match(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
) -> dict[str, bool]:
    return {
        "bot": config.selected_bot_digest == _sha256_digest(runtime.config.bot_id),
        "owner": (
            config.selected_owner_user_id_digest == _sha256_digest(runtime.config.user_id)
        ),
        "environment": bool(
            config.environment and config.environment in config.environment_allowlist
        ),
    }


def _safe_gate2_digest(value: object) -> str | None:
    return value if _is_sha256_digest(value) else None


def _minimal_gate2_exception_chain(
    *,
    gate_error: str = "python_exception",
    exception_stage: str = "gate2_handler",
    exception_class: str | None = None,
    request_digest: str | None = None,
    body_digest: str | None = None,
    chain_build_exception: Exception | None = None,
) -> dict[str, object]:
    chain: dict[str, object] = {
        "source": "python",
        "stage": "exception_handler",
        "chainBuildFailed": chain_build_exception is not None,
        "gateError": _safe_gate2_chain_label(gate_error) or "python_exception",
        "exceptionStage": (
            _safe_gate2_chain_label(exception_stage) or "gate2_handler"
        ),
        "exceptionClass": _safe_gate2_chain_label(exception_class) or "Exception",
    }
    safe_request_digest = _safe_gate2_digest(request_digest)
    if safe_request_digest is not None:
        chain["requestDigest"] = safe_request_digest
    safe_body_digest = _safe_gate2_digest(body_digest)
    if safe_body_digest is not None:
        chain["bodyDigest"] = safe_body_digest
    if chain_build_exception is not None:
        chain["chainBuildExceptionClass"] = (
            _safe_gate2_chain_label(type(chain_build_exception).__name__)
            or "Exception"
        )
    return chain


_GATE2_PARENT_CREATE_LABEL_FIELDS = frozenset(
    {
        "sandboxRootShapeKind",
        "parentCreateStage",
        "parentCreateDeniedReason",
        "componentRole",
    }
)


_GATE2_PARENT_CREATE_COUNT_FIELDS = frozenset(
    {"rootSegmentCount", "safeNamespaceSegmentCount", "componentIndex"}
)


_GATE2_PARENT_CREATE_BOOL_FIELDS = frozenset(
    {
        "approvedParentMatched",
        "finalRootNameMatched",
        "mkdirAttempted",
        "mkdirFailed",
        "openNoFollowFailed",
    }
)


_GATE2_PARENT_CREATE_SAFE_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


def _gate2_parent_create_safe_label(value: object) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    return label if _GATE2_PARENT_CREATE_SAFE_LABEL_RE.fullmatch(label) else "redacted"


def _gate2_parent_create_diagnostics_payload(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    raw_value = value
    if hasattr(value, "model_dump"):
        raw_value = value.model_dump(by_alias=True, mode="json")
    if not isinstance(raw_value, Mapping):
        return None

    diagnostics: dict[str, object] = {}
    for field in _GATE2_PARENT_CREATE_LABEL_FIELDS:
        label = _gate2_parent_create_safe_label(raw_value.get(field))
        if label is None:
            return None
        diagnostics[field] = label
    for field in _GATE2_PARENT_CREATE_COUNT_FIELDS:
        count_value = raw_value.get(field)
        if not isinstance(count_value, int) or isinstance(count_value, bool):
            return None
        if count_value < 0 or count_value > 256:
            return None
        diagnostics[field] = count_value
    for field in _GATE2_PARENT_CREATE_BOOL_FIELDS:
        bool_value = raw_value.get(field)
        if not isinstance(bool_value, bool):
            return None
        diagnostics[field] = bool_value
    return diagnostics


def _gate2_failure_chain(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
    *,
    gate_error: str | None = None,
    request_digest_match: bool | None = None,
    body_digest_match: bool | None = None,
    sandbox_request_validation: str = "not_attempted",
    sandbox_result_status: str | None = None,
    sandbox_result_reason: str | None = None,
    durable_evidence_record_attempted: bool = False,
    exception_stage: str | None = None,
    exception_class: str | None = None,
    request_digest: str | None = None,
    body_digest: str | None = None,
    parent_create_diagnostics: object = None,
) -> dict[str, object]:
    chain: dict[str, object] = {
        "selectedProviderEnabled": config.selected_mutation_provider_enabled,
        "gateError": _safe_gate2_chain_label(gate_error),
        "requestDigestMatch": request_digest_match,
        "bodyDigestMatch": body_digest_match,
        "scopeMatch": _gate2_scope_match(runtime, config),
        "sandboxRequestValidation": _safe_gate2_chain_label(
            sandbox_request_validation
        )
        or "not_attempted",
        "sandboxResultStatus": _safe_gate2_chain_label(sandbox_result_status),
        "sandboxResultReason": _safe_gate2_chain_label(sandbox_result_reason),
        "durableEvidenceStorePresent": config.durable_evidence_store is not None,
        "durableEvidenceRecordAttempted": durable_evidence_record_attempted,
    }
    if exception_stage is not None:
        chain["exceptionStage"] = (
            _safe_gate2_chain_label(exception_stage) or "unexpected_exception"
        )
    if exception_class is not None:
        chain["exceptionClass"] = _safe_gate2_chain_label(exception_class) or "Exception"
    safe_request_digest = _safe_gate2_digest(request_digest)
    if safe_request_digest is not None:
        chain["requestDigest"] = safe_request_digest
    safe_body_digest = _safe_gate2_digest(body_digest)
    if safe_body_digest is not None:
        chain["bodyDigest"] = safe_body_digest
    safe_parent_diagnostics = _gate2_parent_create_diagnostics_payload(
        parent_create_diagnostics
    )
    if safe_parent_diagnostics is not None:
        chain.update(safe_parent_diagnostics)
    return chain


def _gate2_exception_response(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
    *,
    exc: Exception,
    request_digest_match: bool | None = None,
    body_digest_match: bool | None = None,
    sandbox_request_validation: str = "not_attempted",
    sandbox_result_status: str | None = None,
    sandbox_result_reason: str | None = None,
    durable_evidence_record_attempted: bool = False,
    exception_stage: str = "gate2_handler",
    request_digest: str | None = None,
    body_digest: str | None = None,
) -> JSONResponse:
    try:
        failure_chain = _gate2_failure_chain(
            runtime,
            config,
            gate_error="python_exception",
            request_digest_match=request_digest_match,
            body_digest_match=body_digest_match,
            sandbox_request_validation=sandbox_request_validation,
            sandbox_result_status=sandbox_result_status,
            sandbox_result_reason=sandbox_result_reason,
            durable_evidence_record_attempted=durable_evidence_record_attempted,
            exception_stage=exception_stage,
            exception_class=type(exc).__name__,
            request_digest=request_digest,
            body_digest=body_digest,
        )
    except Exception as chain_exc:
        failure_chain = _minimal_gate2_exception_chain(
            gate_error="python_exception",
            exception_stage=exception_stage,
            exception_class=type(exc).__name__,
            request_digest=request_digest,
            body_digest=body_digest,
            chain_build_exception=chain_exc,
        )
    content: dict[str, object] = {
        "gate": "gate2_sandbox_workspace_canary",
        "status": "gate2_sandbox_workspace_canary_failed",
        "fallbackStatus": "fallback_to_typescript",
        "responseAuthority": "typescript",
        "routeDecision": "typescript_fallback",
        "reason": "python_exception",
        "runtime": runtime.config.runtime,
        "runtimeEngine": runtime.config.runtime_engine,
        "diagnosticOnly": True,
        "localOnly": True,
        "fakeOnly": True,
        "gate2FailureChain": failure_chain,
        "adk": {
            "available": runtime.adk_boundary.available,
            "invoked": False,
        },
    }
    return JSONResponse(status_code=503, content=content)


def _gate2_response_extra(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
    **chain_overrides: object,
) -> dict[str, object]:
    gate_error = _safe_gate2_chain_label(chain_overrides.get("gate_error"))
    try:
        failure_chain = _gate2_failure_chain(
            runtime,
            config,
            gate_error=gate_error,
            request_digest_match=_gate2_optional_bool(
                chain_overrides.get("request_digest_match")
            ),
            body_digest_match=_gate2_optional_bool(
                chain_overrides.get("body_digest_match")
            ),
            sandbox_request_validation=str(
                chain_overrides.get("sandbox_request_validation") or "not_attempted"
            ),
            sandbox_result_status=_safe_gate2_chain_label(
                chain_overrides.get("sandbox_result_status")
            ),
            sandbox_result_reason=_safe_gate2_chain_label(
                chain_overrides.get("sandbox_result_reason")
            ),
            durable_evidence_record_attempted=(
                chain_overrides.get("durable_evidence_record_attempted") is True
            ),
            request_digest=_safe_gate2_digest(chain_overrides.get("request_digest")),
            body_digest=_safe_gate2_digest(chain_overrides.get("body_digest")),
            parent_create_diagnostics=chain_overrides.get(
                "parent_create_diagnostics"
            ),
        )
    except Exception as chain_exc:
        failure_chain = _minimal_gate2_exception_chain(
            gate_error=gate_error or "python_error",
            exception_stage="gate2_response_extra",
            exception_class="Exception",
            request_digest=_safe_gate2_digest(chain_overrides.get("request_digest")),
            body_digest=_safe_gate2_digest(chain_overrides.get("body_digest")),
            chain_build_exception=chain_exc,
        )
    return {
        "gate": "gate2_sandbox_workspace_canary",
        "gate2FailureChain": failure_chain,
    }


def _gate2_request_digest_status(
    request: Request,
    payload: Mapping[str, Any],
    *,
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
) -> tuple[str | None, bool, bool]:
    computed_digest = _build_gate2_request_digest(
        payload,
        bot_id=runtime.config.bot_id,
        owner_user_id=runtime.config.user_id,
        environment=config.environment,
    )
    header_digest = request.headers.get("x-gate2-canary-request-digest")
    body_digest = request.headers.get("x-gate2-canary-body-digest")
    body_digest_match = body_digest == _build_gate2_body_digest(payload)
    request_digest_match = header_digest == computed_digest
    if not request_digest_match or not body_digest_match:
        return None, request_digest_match, body_digest_match
    return computed_digest, True, True


def _run_gate2_sandbox_workspace_canary_chat(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
    payload: Mapping[str, Any],
    *,
    request: Request,
) -> JSONResponse:
    try:
        return _run_gate2_sandbox_workspace_canary_chat_impl(
            runtime,
            config,
            payload,
            request=request,
        )
    except Exception as exc:
        return _gate2_exception_response(
            runtime,
            config,
            exception_stage="gate2_handler",
            exc=exc,
            request_digest=request.headers.get("x-gate2-canary-request-digest"),
            body_digest=request.headers.get("x-gate2-canary-body-digest"),
        )


def _run_gate2_sandbox_workspace_canary_chat_impl(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
    payload: Mapping[str, Any],
    *,
    request: Request,
) -> JSONResponse:
    gate_error = _gate2_canary_gate_error(runtime, config)
    if gate_error is not None:
        status_code = 409 if gate_error == "invalid_authority" else 503
        return _fallback_response(
            status_code=status_code,
            status=gate_error,
            reason=_reason_for_gate_error(gate_error),
            runtime=runtime,
            extra_content=_gate2_response_extra(
                runtime,
                config,
                gate_error=gate_error,
            ),
        )
    canary_payload = payload.get("gate2Canary") if isinstance(payload, Mapping) else None
    if payload.get("gate") != "gate2_sandbox_workspace_canary" or not isinstance(
        canary_payload,
        Mapping,
    ):
        return _fallback_response(
            status_code=400,
            status="python_error",
            reason="malformed_gate2_canary_request",
            runtime=runtime,
            extra_content=_gate2_response_extra(
                runtime,
                config,
                sandbox_request_validation="failed",
            ),
        )
    (
        request_digest,
        request_digest_match,
        body_digest_match,
    ) = _gate2_request_digest_status(
        request,
        payload,
        runtime=runtime,
        config=config,
    )
    if request_digest is None:
        return _fallback_response(
            status_code=400,
            status="python_error",
            reason="gate2_request_digest_mismatch",
            runtime=runtime,
            extra_content=_gate2_response_extra(
                runtime,
                config,
                request_digest_match=request_digest_match,
                body_digest_match=body_digest_match,
            ),
        )
    try:
        canary_request = Gate2SandboxCanaryRequest(
            requestDigest=request_digest,
            action=str(canary_payload.get("action") or ""),
            relativePath=str(canary_payload.get("relativePath") or ""),
            content=str(canary_payload.get("content") or ""),
            idempotencyKey=str(canary_payload.get("idempotencyKey") or "gate2-loop-a"),
            patchDigest=canary_payload.get("patchDigest"),
        )
    except ValidationError:
        return _fallback_response(
            status_code=400,
            status="python_error",
            reason="malformed_gate2_canary_request",
            runtime=runtime,
            extra_content=_gate2_response_extra(
                runtime,
                config,
                request_digest_match=request_digest_match,
                body_digest_match=body_digest_match,
                sandbox_request_validation="failed",
            ),
        )
    if config.sandbox_root is None:
        # Fail-closed instead of asserting: a selected provider with no sandbox
        # root (and therefore no durable evidence store) must never silently
        # succeed. ``assert`` would also be stripped under ``python -O``.
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="gate2_sandbox_root_unavailable",
            runtime=runtime,
            extra_content=_gate2_response_extra(
                runtime,
                config,
                request_digest_match=request_digest_match,
                body_digest_match=body_digest_match,
                sandbox_request_validation="passed",
            ),
        )
    try:
        result = run_gate2_sandbox_workspace_canary(
            canary_request,
            sandbox_root=config.sandbox_root,
        )
    except Exception as exc:
        return _gate2_exception_response(
            runtime,
            config,
            request_digest_match=request_digest_match,
            body_digest_match=body_digest_match,
            sandbox_request_validation="passed",
            exception_stage="sandbox_canary_execution",
            exc=exc,
            request_digest=request_digest,
            body_digest=request.headers.get("x-gate2-canary-body-digest"),
        )
    result_body = result.model_dump(by_alias=True, mode="json")
    if result_body.get("parentCreateDiagnostics") is None:
        result_body.pop("parentCreateDiagnostics", None)
    parent_create_diagnostics = _gate2_parent_create_diagnostics_payload(
        result.parent_create_diagnostics
    )
    shadow_config = _shadow_generation_route_config(runtime)
    counter_record_present = False
    if shadow_config.counter_store is not None:
        _is_counter_completed = result.status == "completed"
        _is_counter_selected = _is_counter_completed and config.selected_mutation_provider_enabled
        shadow_config.counter_store.record_gate2_sandbox_canary_evidence(
            request_digest=result.request_digest,
            selected_bot_digest=_sha256_digest(runtime.config.bot_id),
            trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
            environment=config.environment,
            status=(
                "gate2_selected_sandbox_canary_completed"
                if _is_counter_selected
                else (
                    "gate2_sandbox_workspace_canary_completed"
                    if _is_counter_completed
                    else "gate2_sandbox_workspace_canary_blocked"
                )
            ),
            reason=result.reason,
            workspace_mutation_receipt_digest=result.mutation_receipt.receipt_digest,
            rollback_receipt_digest=(
                result.rollback_receipt.rollback_digest
                if result.rollback_receipt is not None
                else None
            ),
            sandbox_path_digest=result.sandbox_path_digest,
        )
        counter_record_present = True
    is_completed = result.status == "completed"
    is_selected = is_completed and config.selected_mutation_provider_enabled

    # ── Durable evidence recording (fail-closed for selected path) ──
    durable_evidence_present = False
    durable_evidence_error: str | None = None
    durable_evidence_record_attempted = False
    if is_selected and config.durable_evidence_store is not None:
        durable_evidence_record_attempted = True
        _output_digest = _sha256_digest(
            json.dumps(result_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        evidence_result = config.durable_evidence_store.record_all_evidence(
            request_digest=result.request_digest,
            body_digest=_build_gate2_body_digest(payload),
            selected_bot_digest=_sha256_digest(runtime.config.bot_id),
            trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
            environment=config.environment,
            status="gate2_selected_sandbox_canary_completed",
            reason=result.reason,
            mutation_receipt_digest=result.mutation_receipt.receipt_digest,
            rollback_receipt_digest=(
                result.rollback_receipt.rollback_digest
                if result.rollback_receipt is not None
                else None
            ),
            sandbox_path_digest=result.sandbox_path_digest,
            output_digest=_output_digest,
        )
        durable_evidence_present = evidence_result.success
        if not evidence_result.success:
            durable_evidence_error = evidence_result.error or "evidence_incomplete"
        else:
            # Disk-readback verification: the in-memory result is not enough.
            # The live Gate 2 failure was an HTTP 200 with NO file on disk, so
            # re-read the persisted record and confirm the file exists AND all
            # five evidence categories survived to disk before claiming success.
            verified_present, verify_error = _verify_gate2_durable_evidence_on_disk(
                config.durable_evidence_store,
                request_digest=result.request_digest,
            )
            durable_evidence_present = verified_present
            if not verified_present:
                durable_evidence_error = verify_error or "evidence_not_persisted"

    # Fail-closed: selected path MUST have durable evidence
    if is_selected and not durable_evidence_present:
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason=durable_evidence_error or "durable_evidence_store_unavailable",
            runtime=runtime,
            extra_content=_gate2_response_extra(
                runtime,
                config,
                request_digest_match=request_digest_match,
                body_digest_match=body_digest_match,
                sandbox_request_validation="passed",
                sandbox_result_status=result.status,
                sandbox_result_reason=result.reason,
                durable_evidence_record_attempted=durable_evidence_record_attempted,
                parent_create_diagnostics=parent_create_diagnostics,
            ),
        )

    body: dict[str, object] = {
        **result_body,
        "status": (
            "gate2_selected_sandbox_canary_completed"
            if is_selected
            else (
                "gate2_sandbox_workspace_canary_completed"
                if is_completed
                else "gate2_sandbox_workspace_canary_blocked"
            )
        ),
        "responseAuthority": "python" if is_selected else "typescript",
        "diagnosticOnly": True,
        "localOnly": True,
        "fakeOnly": True,
        "routeDecision": (
            "python_selected_gate2_sandbox"
            if is_selected
            else (
                "python_diagnostic_only"
                if is_completed
                else "python_blocked"
            )
        ),
        "deliveryStatus": (
            "diagnostic_completed" if is_completed else "blocked"
        ),
        "authority": _gate2_sandbox_canary_authority(),
        "gate2Readiness": gate2_readiness_health_metadata(
            runtime.config.gate2_readiness,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
            sandbox_root_readiness=_gate2_selected_sandbox_root_readiness(config),
        ),
        "counter": {
            "status": (
                "gate2_sandbox_canary_recorded"
                if counter_record_present
                else "counter_store_unavailable"
            ),
            "requestDigest": result.request_digest,
            "pythonCounterRecordPresent": counter_record_present,
        },
        "durableEvidence": {
            "present": durable_evidence_present,
            "error": durable_evidence_error,
        },
        "gate2FailureChain": _gate2_failure_chain(
            runtime,
            config,
            request_digest_match=request_digest_match,
            body_digest_match=body_digest_match,
            sandbox_request_validation="passed",
            sandbox_result_status=result.status,
            sandbox_result_reason=result.reason,
            durable_evidence_record_attempted=durable_evidence_record_attempted,
            parent_create_diagnostics=parent_create_diagnostics,
        ),
        "adk": {"available": runtime.adk_boundary.available, "invoked": False},
        "activeTools": [],
    }
    if result.status == "completed":
        body["choices"] = [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Sandbox workspace check completed.",
                },
                "finish_reason": "stop",
            }
        ]
    return JSONResponse(
        status_code=200 if result.status == "completed" else 409,
        content=body,
    )


def _gate2_canary_gate_error(
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
) -> str | None:
    if config.kill_switch_enabled is not False:
        return "python_disabled"
    if config.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "python_disabled"
    if config.selected_owner_user_id_digest != _sha256_digest(runtime.config.user_id):
        return "python_disabled"
    if not config.environment or config.environment not in config.environment_allowlist:
        return "python_disabled"
    if config.sandbox_root is None:
        return "python_disabled"
    gate2 = gate2_readiness_health_metadata(
        runtime.config.gate2_readiness,
        bot_id=runtime.config.bot_id,
        user_id=runtime.config.user_id,
    )
    if gate2.get("readinessReady") is not True:
        return "python_disabled"
    if gate2.get("productionWorkspaceMutationAllowed") is not False:
        return "invalid_authority"
    if gate2.get("toolHostDispatchAllowed") is not False:
        return "invalid_authority"
    authority = runtime.config.authority
    if authority.workspace_mutation_allowed is not False:
        return "invalid_authority"
    if authority.transcript_write_allowed is not False or authority.sse_write_allowed is not False:
        return "invalid_authority"
    if authority.channel_write_allowed is not False or authority.db_write_allowed is not False:
        return "invalid_authority"
    if authority.child_execution_allowed is not False:
        return "invalid_authority"
    if authority.mission_runtime_allowed is not False:
        return "invalid_authority"
    return None


def _gate2_request_digest(
    request: Request,
    payload: Mapping[str, Any],
    *,
    runtime: OpenMagiRuntime,
    config: Gate2SandboxWorkspaceCanaryConfig,
) -> str | None:
    computed_digest = _build_gate2_request_digest(
        payload,
        bot_id=runtime.config.bot_id,
        owner_user_id=runtime.config.user_id,
        environment=config.environment,
    )
    header_digest = request.headers.get("x-gate2-canary-request-digest")
    body_digest = request.headers.get("x-gate2-canary-body-digest")
    if header_digest is None or body_digest is None:
        return None
    if body_digest != _build_gate2_body_digest(payload):
        return None
    return computed_digest if header_digest == computed_digest else None


def _build_gate2_request_digest(
    payload: Mapping[str, Any],
    *,
    bot_id: str,
    owner_user_id: str,
    environment: str,
) -> str:
    canary = payload.get("gate2Canary") if isinstance(payload, Mapping) else None
    canary_payload = canary if isinstance(canary, Mapping) else {}
    relative_path = str(canary_payload.get("relativePath") or "").strip()
    content = canary_payload.get("content")
    content_text = content if isinstance(content, str) else ""
    idempotency_key = str(canary_payload.get("idempotencyKey") or "").strip()
    patch_digest = _safe_optional_gate2_digest(canary_payload.get("patchDigest"))
    summary = {
        "gate": "gate2_sandbox_workspace_canary",
        "botId": str(bot_id or "").strip(),
        "ownerUserId": str(owner_user_id or "").strip(),
        "environment": str(environment or "").strip(),
        "messages": _summarize_gate2_messages(payload.get("messages")),
        "action": str(canary_payload.get("action") or "").strip(),
        "relativePathDigest": _sha256_digest(relative_path) if relative_path else None,
        "contentDigest": _sha256_digest(content_text) if content_text else None,
        "idempotencyKeyDigest": (
            _sha256_digest(idempotency_key) if idempotency_key else None
        ),
        "patchDigest": patch_digest,
        "bodyDigest": _build_gate2_body_digest(payload),
    }
    return _sha256_digest(
        json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _build_gate2_body_digest(payload: Mapping[str, Any]) -> str:
    return _sha256_digest(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    )


def _summarize_gate2_messages(messages: object) -> list[dict[str, object]]:
    if not isinstance(messages, list):
        return []
    summary: list[dict[str, object]] = []
    for message in messages[-8:]:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "user").strip().lower()
        safe_role = role if role in {"system", "assistant", "user"} else "user"
        text = _gate2_message_content_to_text(message.get("content"))
        summary.append(
            {
                "role": safe_role,
                "contentDigest": _sha256_digest(text) if text else None,
                "contentBytes": len(text.encode("utf-8")),
            }
        )
    return summary


def _gate2_message_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "\n".join(part for part in parts if part)
    return ""


def _safe_optional_gate2_digest(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if _is_sha256_digest(text) else _sha256_digest(text)


def _gate2_sandbox_canary_authority() -> dict[str, bool]:
    return {
        "userVisibleOutputAllowed": False,
        "canaryRoutingAllowed": False,
        "toolDispatchAllowed": False,
        "readOnlyToolDispatchAllowed": False,
        "writeMutationAuthorityAllowed": False,
        "workspaceMutationAllowed": False,
        "memoryWriteAllowed": False,
        "browserWebNetworkAllowed": False,
        "channelWritesAllowed": False,
        "dbWritesAllowed": False,
        "transcriptWritesAllowed": False,
        "sseWritesAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "schedulerMutationAllowed": False,
        "evidenceBlockModeAllowed": False,
    }


def _record_gate2_sandbox_workspace_delivery_receipt(
    *,
    runtime: OpenMagiRuntime,
    payload: Gate5BUserVisibleDeliveryReceiptPayload,
) -> JSONResponse:
    route_config = _gate2_sandbox_canary_config(runtime)
    if not route_config.enabled:
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason="canary_gate_disabled",
            runtime=runtime,
        )
    gate_error = _gate2_canary_gate_error(runtime, route_config)
    if gate_error is not None:
        status_code = 409 if gate_error == "invalid_authority" else 503
        return _fallback_response(
            status_code=status_code,
            status=gate_error,
            reason=_reason_for_gate_error(gate_error),
            runtime=runtime,
        )
    shadow_config = _shadow_generation_route_config(runtime)
    if shadow_config.counter_store is None:
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="counter_store_unavailable",
            runtime=runtime,
        )
    scope_error = _gate2_receipt_scope_error(
        payload=payload,
        runtime=runtime,
        route_config=route_config,
    )
    if scope_error is not None:
        return JSONResponse(
            status_code=409,
            content={
                "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                "status": "receipt_rejected",
                "receiptStatus": _gate2_receipt_rejection_status(scope_error),
                "requestDigest": payload.request_digest,
                "deliveryStatus": payload.delivery_status,
                "responseAuthority": "typescript",
                "reason": scope_error,
                "diagnosticOnly": True,
                "localOnly": True,
                "counter": {
                    "pythonCounterRecordPresent": payload.python_counter_record_present,
                },
            },
        )
    evidence_error = shadow_config.counter_store.gate2_sandbox_canary_evidence_error(
        request_digest=payload.request_digest,
        selected_bot_digest=_sha256_digest(runtime.config.bot_id),
        trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
        environment=route_config.environment,
        workspace_mutation_receipt_digest=payload.workspace_mutation_receipt_digest,
        rollback_receipt_digest=payload.rollback_receipt_digest,
        sandbox_path_digest=payload.sandbox_path_digest,
    )
    if evidence_error is not None:
        return JSONResponse(
            status_code=409,
            content={
                "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                "status": "receipt_rejected",
                "receiptStatus": _gate2_receipt_rejection_status(evidence_error),
                "requestDigest": payload.request_digest,
                "deliveryStatus": payload.delivery_status,
                "responseAuthority": "typescript",
                "reason": evidence_error,
                "diagnosticOnly": True,
                "localOnly": True,
                "counter": {
                    "pythonCounterRecordPresent": payload.python_counter_record_present,
                },
            },
        )

    receipt = shadow_config.counter_store.record_delivery_receipt(
        request_digest=payload.request_digest,
        selected_bot_digest=_sha256_digest(runtime.config.bot_id),
        trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
        environment=route_config.environment,
        delivery_status=payload.delivery_status,
        reason=payload.reason,
        body_digest=payload.body_digest,
        route_decision=payload.route_decision,
        response_authority=payload.response_authority,
        gate=payload.gate,
        served_at=payload.served_at,
        completed_at=payload.completed_at,
        fallback_reason=payload.fallback_reason,
        sse_frame_count=payload.sse_frame_count,
        tool_receipt_count=payload.tool_receipt_count,
        model_attempt_count=payload.model_attempt_count,
        provider_request_count=payload.provider_request_count,
        expected_model_attempt_count=payload.expected_model_attempt_count,
        output_digest=payload.output_digest,
        workspace_mutation_receipt_digest=payload.workspace_mutation_receipt_digest,
        rollback_receipt_digest=payload.rollback_receipt_digest,
        sandbox_path_digest=payload.sandbox_path_digest,
        python_attempted=payload.python_attempted,
        python_counter_record_present=payload.python_counter_record_present,
        context_continuity=_context_continuity_chat_diagnostic(runtime),
    )
    status_code = 404 if receipt.status == "not_found" else 202
    return JSONResponse(
        status_code=status_code,
        content={
            "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
            "status": (
                "receipt_not_found"
                if receipt.status == "not_found"
                else "receipt_recorded"
            ),
            "receiptStatus": receipt.status,
            "requestDigest": payload.request_digest,
            "deliveryStatus": payload.delivery_status,
            "responseAuthority": "typescript",
            "diagnosticOnly": True,
            "localOnly": True,
            "counter": {
                "deliveryReceiptCount": receipt.delivery_receipt_count,
                "deliveryDuplicateCount": receipt.delivery_duplicate_count,
                "deliveryConflictCount": receipt.delivery_conflict_count,
                "pythonCounterRecordPresent": payload.python_counter_record_present,
            },
        },
    )


def _gate2_receipt_rejection_status(reason: str) -> str:
    if reason == "python_counter_record_required":
        return "python_counter_record_required"
    if reason in {
        "workspace_mutation_receipt_required",
        "rollback_receipt_required",
        "sandbox_path_digest_required",
        "gate2_evidence_mismatch",
        "gate2_evidence_not_completed",
    }:
        return "evidence_error"
    return "scope_mismatch"


def _gate2_receipt_scope_error(
    *,
    payload: Gate5BUserVisibleDeliveryReceiptPayload,
    runtime: OpenMagiRuntime,
    route_config: Gate2SandboxWorkspaceCanaryConfig,
) -> str | None:
    if payload.delivery_status != "served_to_client":
        return "served_delivery_status_required"
    if payload.response_authority != "python":
        return "python_response_authority_required"
    if payload.route_decision != "python_selected_gate2_sandbox":
        return "gate2_route_decision_required"
    if not payload.python_attempted or not payload.python_counter_record_present:
        return "python_counter_record_required"
    if payload.selected_scope is None:
        return "selected_scope_required"
    if payload.selected_scope.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "selected_scope_mismatch"
    if (
        payload.selected_scope.selected_owner_user_id_digest
        != _sha256_digest(runtime.config.user_id)
    ):
        return "selected_scope_mismatch"
    if payload.selected_scope.environment != route_config.environment:
        return "selected_scope_mismatch"
    if payload.workspace_mutation_receipt_digest is None:
        return "workspace_mutation_receipt_required"
    if payload.rollback_receipt_digest is None:
        return "rollback_receipt_required"
    if payload.sandbox_path_digest is None:
        return "sandbox_path_digest_required"
    return None
