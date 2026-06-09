from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from json import JSONDecodeError
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from magi_agent.evidence.gate2_durable_evidence import (
    Gate2DurableEvidenceStore,
)
from magi_agent.evidence.gate1a_egress_correlation import (
    GATE1A_EGRESS_CORRELATION_MODE,
    GATE1A_EGRESS_TELEMETRY_SOURCE,
    Gate1AEgressCorrelationContext,
)
from magi_agent.evidence.observed_egress import (
    ObservedEgressEvidence,
    get_observed_egress_evidence_provider,
    observed_egress_diagnostics,
)
from magi_agent.gates.gate1a_readonly_tools import (
    GATE1A_FORBIDDEN_TOOL_NAMES,
    GATE1A_READONLY_TOOL_NAMES,
    Gate1AReadOnlyToolBundle,
    Gate1AReadOnlyToolConfig,
    build_gate1a_readonly_tool_bundle,
)
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolBundle,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)
from magi_agent.config.env import is_egress_gate_enabled, is_read_quality_enabled
from magi_agent.introspection.egress_gate import EgressVerifierStatus
from magi_agent.gates.gate2_readiness import gate2_readiness_health_metadata
from magi_agent.gates.gate8_readiness import gate8_readiness_health_metadata
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.session_identity import _memory_mode_from_header

if TYPE_CHECKING:
    from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.runtime.public_events import (
    tool_end_event,
    tool_progress_event,
    tool_start_event,
    turn_phase_event,
)
from magi_agent.research.research_first_canary import (
    build_research_first_selected_response,
    research_first_selected_canary_active,
)
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)
from magi_agent.recipes.materializer import RecipeMaterializer
from magi_agent.shadow.gate2_activation_loop_a import (
    Gate2SandboxCanaryRequest,
    Gate2SandboxRootReadiness,
    check_gate2_sandbox_root_readiness,
    run_gate2_sandbox_workspace_canary,
)
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    AdkPrimitivesLoader,
    run_gate5b4c3_live_runner_boundary_async,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterReservation,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)


MockedChatRunner = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ClientDisconnectedProbe = Callable[[Request], bool | Awaitable[bool]]
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE = re.compile(
    r"(?:"
    r"Authorization:|Bearer\s+\S+|(?:Cookie|Set-Cookie):|"
    r"sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|"
    r"\b(?:api[_-]?key|token|secret|password|session[_-]?key)\b|"
    r"\b(?:prompt|output|request[_-]?body|response[_-]?body)\s*[:=]\s*\S+|"
    r"/(?:Users|private|workspace|data/bots|var/lib/kubelet|mnt)\S*|"
    r"https?://\S+"
    r")",
    re.IGNORECASE,
)
_INCOMPLETE_RUNNER_OUTPUT_RE = re.compile(
    r"(?:"
    r"잠시만\s*기다|"
    r"기다려\s*주|"
    r"조금만\s*더\s*기다|"
    r"완료되면|"
    r"전달(?:드리|해)\s*겠|"
    r"진행\s*중|"
    r"처리\s*중|"
    r"실행\s*중|"
    r"작업\s*중|"
    r"please\s+wait|"
    r"still\s+working|"
    r"in\s+progress|"
    r"once\s+(?:it\s+is\s+)?complete|"
    r"when\s+(?:it\s+is\s+)?complete|"
    r"i(?:'|’)ll\s+(?:continue|update)|"
    r"i\s+will\s+(?:continue|update)|"
    r"will\s+(?:continue|update|send|share)\b"
    r")",
    re.IGNORECASE,
)
_CONTEXT_REASON_CODE_FORBIDDEN_RE = re.compile(
    r"(?:"
    r"Authorization|Bearer|Cookie|Set-Cookie|"
    r"\b(?:api[_-]?key|token|secret|password|session[_-]?key|credential)\b|"
    r"private|"
    r"sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|"
    r"/(?:Users|private|workspace|data/bots|var/lib/kubelet|mnt)\S*|"
    r"https?://\S+|"
    r"^[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}$|"
    r"^[A-Za-z0-9_-]{32,}$"
    r")",
    re.IGNORECASE,
)
_FALLBACK_RECEIPT_SCOPE_GATES = frozenset(
    {
        "gate1a_readonly_tools",
        "gate7_5_context_continuity",
    }
)
_APP_CHANNEL_HISTORY_SCHEMA = "openmagi.app_channel_history.v1"
_FIRST_PARTY_HARNESS_RECIPE_PACK_IDS = (
    "openmagi.context-safety",
    "openmagi.evidence",
    "openmagi.agent-methodology",
    "openmagi.superpowers-compat",
    "openmagi.web-acquisition",
    "openmagi.research",
    "openmagi.dev-coding",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.memory-agentmemory",
    "openmagi.channel-delivery",
    "openmagi.office-automation",
    "openmagi.artifact-delivery",
    "openmagi.spreadsheet-automation",
    "openmagi.browser-automation",
    "openmagi.document-review",
    "openmagi.lightweight-scripting",
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
class Gate5BUserVisibleChatRouteConfig:
    enabled: bool
    kill_switch_enabled: bool
    selected_bot_digest: str
    selected_owner_user_id_digest: str
    environment: str
    environment_allowlist: tuple[str, ...]
    mocked_runner: MockedChatRunner | None
    adk_primitives_loader: AdkPrimitivesLoader | None
    client_disconnected_probe: ClientDisconnectedProbe | None

    def __init__(
        self,
        enabled: object = False,
        kill_switch_enabled: object = True,
        selected_bot_digest: str = "",
        selected_owner_user_id_digest: str = "",
        environment: str = "",
        environment_allowlist: tuple[str, ...] = (),
        mocked_runner: MockedChatRunner | None = None,
        adk_primitives_loader: AdkPrimitivesLoader | None = None,
        client_disconnected_probe: ClientDisconnectedProbe | None = None,
        *,
        killSwitchEnabled: object | None = None,
        selectedBotDigest: str | None = None,
        selectedOwnerUserIdDigest: str | None = None,
        environmentAllowlist: tuple[str, ...] | None = None,
        mockedRunner: MockedChatRunner | None = None,
        adkPrimitivesLoader: AdkPrimitivesLoader | None = None,
        clientDisconnectedProbe: ClientDisconnectedProbe | None = None,
    ) -> None:
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
        object.__setattr__(self, "mocked_runner", mockedRunner or mocked_runner)
        object.__setattr__(
            self,
            "adk_primitives_loader",
            adkPrimitivesLoader or adk_primitives_loader,
        )
        object.__setattr__(
            self,
            "client_disconnected_probe",
            clientDisconnectedProbe or client_disconnected_probe,
        )


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


def build_gate5b_user_visible_chat_route_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate5BUserVisibleChatRouteConfig:
    if _is_true(env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED")):
        return Gate5BUserVisibleChatRouteConfig(
            enabled=True,
            killSwitchEnabled=_env_bool_default_true(
                env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH")
            ),
            selectedBotDigest=env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_SELECTED_BOT_DIGEST",
                "",
            ).strip(),
            selectedOwnerUserIdDigest=env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_TRUSTED_OWNER_USER_ID_DIGEST",
                "",
            ).strip(),
            environment=env.get(
                "CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT",
                "",
            ).strip(),
            environmentAllowlist=_csv_values(
                env.get("CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST", "")
            ),
        )
    return Gate5BUserVisibleChatRouteConfig(
        enabled=_is_true(env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED")),
        killSwitchEnabled=_is_true(
            env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH")
        ),
        selectedBotDigest=env.get(
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST",
            "",
        ).strip(),
        selectedOwnerUserIdDigest=env.get(
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
            "",
        ).strip(),
        environment=env.get(
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
            "",
        ).strip(),
        environmentAllowlist=_csv_values(
            env.get("CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST", "")
        ),
    )


def build_gate1a_readonly_tools_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate1AReadOnlyToolConfig:
    del runtime_config
    return Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENABLED")
            ),
            "killSwitchEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_KILL_SWITCH", "1")
            ),
            "routeAttachmentEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ROUTE_ATTACHMENT", "1")
            ),
            "selectedBotDigest": env.get(
                "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_SELECTED_BOT_DIGEST",
                "",
            ).strip(),
            "selectedOwnerDigest": env.get(
                "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_TRUSTED_OWNER_USER_ID_DIGEST",
                "",
            ).strip(),
            "environment": env.get(
                "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENVIRONMENT",
                "local",
            ).strip()
            or "local",
            "environmentAllowlist": _csv_values(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENV_ALLOWLIST", "")
            ),
            "allowedToolNames": _csv_values(
                env.get(
                    "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ALLOWLIST",
                    ",".join(GATE1A_READONLY_TOOL_NAMES),
                )
            ),
            "maxToolCallsPerTurn": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_CALLS_PER_TURN"),
                fallback=8,
            ),
            "maxPerToolOutputBytes": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_PER_TOOL_BYTES"),
                fallback=4096,
            ),
            "maxAggregateOutputBytes": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_AGGREGATE_BYTES"),
                fallback=16384,
            ),
        }
    )


def build_gate5b_full_toolhost_config_from_env(
    env: Mapping[str, str],
    runtime_config: object,
) -> Gate5BFullToolHostConfig:
    del runtime_config
    from magi_agent.config.env import (
        apply_patch_enabled,
        is_format_on_write_enabled,
        parse_lsp_diagnostics_env,
    )

    lsp_diagnostics = parse_lsp_diagnostics_env(env)
    return Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED")
            ),
            "killSwitchEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_KILL_SWITCH", "1")
            ),
            "routeAttachmentEnabled": _is_true(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ROUTE_ATTACHMENT", "1")
            ),
            "selectedBotDigest": env.get(
                "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_SELECTED_BOT_DIGEST",
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST",
                    "",
                ),
            ).strip(),
            "selectedOwnerDigest": env.get(
                "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_TRUSTED_OWNER_USER_ID_DIGEST",
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST",
                    "",
                ),
            ).strip(),
            "environment": env.get(
                "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENVIRONMENT",
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
                    "local",
                ),
            ).strip()
            or "local",
            "environmentAllowlist": _csv_values(
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENV_ALLOWLIST",
                    env.get(
                        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST",
                        "",
                    ),
                )
            ),
            "allowedToolNames": _csv_values(
                env.get(
                    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ALLOWLIST",
                    ",".join(GATE5B_FULL_TOOLHOST_TOOL_NAMES),
                )
            ),
            "maxToolCallsPerTurn": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_MAX_CALLS_PER_TURN"),
                fallback=16,
            ),
            "maxPerToolOutputBytes": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_MAX_PER_TOOL_BYTES"),
                fallback=8192,
            ),
            "commandTimeoutMs": _int_env(
                env.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_COMMAND_TIMEOUT_MS"),
                fallback=5000,
            ),
            "formatOnWriteEnabled": is_format_on_write_enabled(env),
            "lspDiagnosticsEnabled": lsp_diagnostics.enabled,
            "lspDiagnosticsCap": lsp_diagnostics.cap,
            "lspDiagnosticsTimeoutMs": lsp_diagnostics.timeout_ms,
            "readQualityEnabled": is_read_quality_enabled(env),
            "readMaxLines": _int_env(
                env.get("MAGI_READ_QUALITY_MAX_LINES"),
                fallback=2000,
            ),
            "ripgrepEnabled": _is_true(env.get("MAGI_RIPGREP_ENABLED")),
            "applyPatchEnabled": apply_patch_enabled(env),
            "applyPatchModelId": (
                env.get("CORE_AGENT_MODEL", "").strip()
            ),
        }
    )


_FALSE_RUNTIME_AUTHORITY_KEYS = (
    "transcriptWritesAllowed",
    "sseWritesAllowed",
    "channelWritesAllowed",
    "dbWritesAllowed",
    "workspaceMutationAllowed",
    "childExecutionAllowed",
    "missionRuntimeAllowed",
    "evidenceBlockModeAllowed",
)
_FALSE_RESPONSE_AUTHORITY_KEYS = (
    "memoryWriteAllowed",
    "toolDispatchAllowed",
    *_FALSE_RUNTIME_AUTHORITY_KEYS,
)
_PUBLIC_IDENTITY_POLICY = {
    "schemaVersion": "gate5b.publicIdentityPolicy.v1",
    "canonicalName": "Magi Agent",
    "platformName": "OpenMagi",
    "modelVisibleSystemContext": (
        "You are Magi Agent for OpenMagi. Present the user-visible assistant "
        "identity as Magi Agent / OpenMagi, and keep infrastructure, package, "
        "namespace, deployment, and runtime implementation names out of "
        "model-visible public identity context."
    ),
}
_GATE1A_EGRESS_DISCIPLINE_MODE = "bounded_provider_tunnels"
_GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT = 2
_LEGACY_IDENTITY_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bmagi\s+agent\b", re.IGNORECASE),
        "Magi Agent",
        "legacy_public_identity_normalized",
    ),
    (
        re.compile(r"\bmagi[-_]agent\b|\bmagi-core-agent\b", re.IGNORECASE),
        "OpenMagi runtime",
        "legacy_runtime_identity_normalized",
    ),
)
_MODEL_VISIBLE_CONTEXT_MAX_CHARS = 1_000_000


def _local_chat_route_enabled() -> bool:
    return os.environ.get("MAGI_AGENT_LOCAL_CHAT_ROUTE", "off").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _local_adk_chat_response(
    runtime: OpenMagiRuntime,
    payload: object,
) -> StreamingResponse:
    prompt = _local_chat_prompt_text(payload)
    return StreamingResponse(
        _local_adk_chat_sse(runtime, payload, prompt),
        media_type="text/event-stream",
    )


async def _local_adk_chat_sse(
    runtime: OpenMagiRuntime,
    payload: object,
    prompt: str,
) -> AsyncIterator[str]:
    from magi_agent.cli.contracts import EngineResult
    from magi_agent.cli.wiring import (
        build_headless_runtime,
        local_runner_policy_routing_enabled_from_env,
    )
    from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL

    session_id = _local_chat_string(payload, "sessionId", "local-dashboard")
    turn_id = _local_chat_string(payload, "turnId", f"{session_id}:turn")
    yield _sse_data({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
    yield _sse_event(
        "agent",
        {
            "type": "turn_phase",
            "turnId": turn_id,
            "phase": "executing",
        },
    )
    yield _sse_event(
        "agent",
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "started",
            "label": "Running local ADK",
            "detail": "Local headless engine active",
        },
    )

    # The no-env local fallback injects ``LOCAL_DEV_MODEL_SENTINEL`` as the
    # required ``CORE_AGENT_MODEL``; treat it as "unset" so the headless runner
    # uses the per-provider default model instead of trying to call a
    # nonexistent ``<provider>/local-dev`` model.
    configured_model = runtime.config.model
    model_override = (
        None if configured_model == LOCAL_DEV_MODEL_SENTINEL else configured_model
    )
    headless = build_headless_runtime(
        cwd=os.environ.get("MAGI_AGENT_WORKSPACE") or os.getcwd(),
        permission_mode="bypassPermissions",
        session_id=session_id,
        model=model_override,
        runner_policy_routing_enabled=local_runner_policy_routing_enabled_from_env(),
    )
    cancel = asyncio.Event()
    stream = headless.engine.run_turn_stream(
        None,
        {
            "prompt": prompt,
            "session_id": session_id,
            "turn_id": turn_id,
        },
        cancel=cancel,
        gate=headless.gate,
    )
    async for item in stream:
        if isinstance(item, EngineResult):
            if item.error:
                yield _sse_event(
                    "agent",
                    {
                        "type": "error",
                        "turnId": turn_id,
                        "reason": item.error,
                    },
                )
            break
        event_payload = dict(item.payload)
        yield _sse_event("agent", event_payload)
        delta = _local_runtime_event_delta(event_payload)
        if delta:
            yield _sse_data({"choices": [{"index": 0, "delta": {"content": delta}}]})
    # ── BACKGROUND MEMORY-REVIEW WIRING SEAM (A1, PR5) ──────────────────────
    # This is the turn-finalization point of the live local chat path: the
    # engine stream has drained, so the assistant turn is complete. A periodic
    # background memory review (Hermes-style "save what the model forgot") would
    # be triggered HERE — but DELIBERATELY NOT IN THIS PR, because it needs a
    # live model-backed reviewer and MUST run OFF this hot path so it never
    # blocks the user's turn or the SSE stream. When a live reviewer is added,
    # wire it like this (off-loop, e.g. via the background-task boundary):
    #
    #   from magi_agent.harness.memory_review import (
    #       MemoryReviewConfig, MemoryReviewHarness, should_run_review,
    #   )
    #   from magi_agent.runtime.memory_write_wiring import build_memory_write_host
    #
    #   cfg = MemoryReviewConfig(enabled=...)   # default-OFF; also gated by
    #                                           # MAGI_MEMORY_REVIEW_ENABLED env
    #   if should_run_review(turn_count, interval_turns=cfg.interval_turns,
    #                        enabled=cfg.enabled):
    #       host = build_memory_write_host(
    #           workspace_root=Path(workspace), bot_id=..., user_id=...,
    #       )
    #       # review() is async. We are already inside a live event loop here,
    #       # so schedule it fire-and-forget OFF the hot path — NEVER await it
    #       # inline (that would block the SSE stream / the user's turn):
    #       asyncio.create_task(
    #           MemoryReviewHarness(cfg).review(
    #               transcript, reviewer=<live extractor>, write_host=host,
    #           )
    #       )
    #
    # The harness re-runs the declarative filter + PR2 write gate on every
    # surfaced fact, so even a buggy reviewer cannot persist task-state or write
    # without the memory-write gate being live. Do NOT inline it above. ────────
    yield _sse_data({"choices": [{"index": 0, "finish_reason": "stop"}]})
    yield "data: [DONE]\n\n"


def _local_runtime_event_delta(payload: Mapping[str, object]) -> str:
    for key in ("delta", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _local_chat_string(payload: object, key: str, default: str) -> str:
    if isinstance(payload, Mapping):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _sse_data(payload: Mapping[str, object]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _sse_event(name: str, payload: Mapping[str, object]) -> str:
    return f"event: {name}\n{_sse_data(payload)}"


def _local_chat_prompt_text(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    text_parts: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            for block in content:
                if isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
    return "\n".join(part.strip() for part in text_parts if part.strip())


def register_chat_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if (
            _local_chat_route_enabled()
            and os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on"
        ):
            try:
                payload = await request.json()
            except (JSONDecodeError, ValueError):
                return JSONResponse(
                    status_code=400,
                    content={"error": "malformed_json"},
                )
            return _local_adk_chat_response(runtime, payload)
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return JSONResponse(
                status_code=503,
                content={
                    "error": "chat_route_disabled",
                    "runtime": runtime.config.runtime,
                    "runtimeEngine": runtime.config.runtime_engine,
                },
            )
        gate2_config = _gate2_sandbox_canary_config(runtime)
        if gate2_config.enabled:
            try:
                payload = await request.json()
            except (JSONDecodeError, ValueError):
                return _fallback_response(
                    status_code=400,
                    status="python_error",
                    reason="malformed_json",
                    runtime=runtime,
                )
            if (
                isinstance(payload, Mapping)
                and payload.get("gate") == "gate2_sandbox_workspace_canary"
            ):
                return _run_gate2_sandbox_workspace_canary_chat(
                    runtime,
                    gate2_config,
                    payload,
                    request=request,
                )
        route_config = _route_config(runtime)
        if not route_config.enabled:
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="canary_gate_disabled",
                runtime=runtime,
            )
        try:
            payload = await request.json()
        except (JSONDecodeError, ValueError):
            if not route_config.enabled:
                return _fallback_response(
                    status_code=503,
                    status="python_disabled",
                    reason="canary_gate_disabled",
                    runtime=runtime,
                )
            return _fallback_response(
                status_code=400,
                status="python_error",
                reason="malformed_json",
                runtime=runtime,
            )
        return await run_gate5b_user_visible_chat_response(
            runtime,
            payload,
            request=request,
        )

    @app.post("/v1/chat/inject")
    async def chat_inject(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return JSONResponse(
                status_code=503,
                content={
                    "error": "chat_route_disabled",
                    "reason": "python_inject_unsupported",
                    "fallback": "queue_to_completions",
                    "activeTurnCompatible": False,
                    "responseAuthority": "typescript",
                },
            )
        return JSONResponse(
            status_code=409,
            content={
                "error": "no_active_turn",
                "reason": "python_inject_unsupported",
                "fallback": "queue_to_completions",
                "activeTurnCompatible": False,
                "responseAuthority": "typescript",
            },
        )

    @app.post("/v1/chat/interrupt")
    async def chat_interrupt(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return JSONResponse(
                status_code=503,
                content={
                    "error": "chat_route_disabled",
                    "reason": "python_interrupt_unsupported",
                    "fallback": "typescript_interrupt_required",
                    "activeTurnCompatible": False,
                    "handoffRequested": False,
                    "gateStateOpen": False,
                    "responseAuthority": "typescript",
                },
            )
        try:
            payload = await request.json()
        except (JSONDecodeError, ValueError):
            payload = {}
        handoff_requested = (
            isinstance(payload, Mapping) and payload.get("handoffRequested") is True
        )
        return JSONResponse(
            status_code=409,
            content={
                "error": "no_active_turn",
                "reason": "python_interrupt_unsupported",
                "fallback": "typescript_interrupt_required",
                "activeTurnCompatible": False,
                "handoffRequested": handoff_requested,
                "gateStateOpen": False,
                "responseAuthority": "typescript",
            },
        )

    @app.post("/v1/internal/gate5b/user-visible-delivery-receipts")
    async def gate5b_user_visible_delivery_receipts(
        request: Request,
        payload: Gate5BUserVisibleDeliveryReceiptPayload,
    ) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="chat_route_disabled",
                runtime=runtime,
            )
        if payload.gate == "gate2_sandbox_workspace_canary":
            return _record_gate2_sandbox_workspace_delivery_receipt(
                runtime=runtime,
                payload=payload,
            )
        route_config = _route_config(runtime)
        if not route_config.enabled:
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="canary_gate_disabled",
                runtime=runtime,
            )
        gate_error = _canary_gate_error(runtime, route_config)
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
        fallback_scope_error = _fallback_only_scope_error(
            payload=payload,
            runtime=runtime,
            route_config=route_config,
        )
        if fallback_scope_error is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                    "status": "receipt_rejected",
                    "receiptStatus": "scope_mismatch",
                    "requestDigest": payload.request_digest,
                    "deliveryStatus": payload.delivery_status,
                    "responseAuthority": "typescript",
                    "reason": fallback_scope_error,
                    "diagnosticOnly": True,
                    "localOnly": True,
                },
            )
        research_first_receipt_error = (
            shadow_config.counter_store.gate8_research_first_delivery_receipt_error(
                request_digest=payload.request_digest,
                selected_bot_digest=_sha256_digest(runtime.config.bot_id),
                trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
                environment=route_config.environment,
                delivery_status=payload.delivery_status,
                gate=payload.gate,
                route_decision=payload.route_decision,
                response_authority=payload.response_authority,
                output_digest=payload.output_digest,
                source_ledger_digest=payload.source_ledger_digest,
                final_projection_digest=payload.final_projection_digest,
                research_evidence_status=payload.research_evidence_status,
                citation_evidence_status=payload.citation_evidence_status,
                verifier_evidence_status=payload.verifier_evidence_status,
                final_projection_evidence_status=(
                    payload.final_projection_evidence_status
                ),
                source_inspected_event_count=payload.source_inspected_event_count,
                rule_check_event_count=payload.rule_check_event_count,
            )
        )
        if research_first_receipt_error is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                    "status": "receipt_rejected",
                    "receiptStatus": "receipt_rejected",
                    "requestDigest": payload.request_digest,
                    "deliveryStatus": payload.delivery_status,
                    "responseAuthority": "typescript",
                    "reason": research_first_receipt_error,
                    "diagnosticOnly": True,
                    "localOnly": True,
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
            egress_connect_count=payload.egress_connect_count,
            egress_tunnel_count=payload.egress_tunnel_count,
            egress_discipline_mode=payload.egress_discipline_mode,
            egress_evidence_status=payload.egress_evidence_status,
            egress_evidence_source=payload.egress_evidence_source,
            egress_evidence_redaction_status=payload.egress_evidence_redaction_status,
            egress_evidence_decision_reason=payload.egress_evidence_decision_reason,
            model_attempt_digest=payload.model_attempt_digest,
            max_provider_tunnels_per_model_attempt=(
                payload.max_provider_tunnels_per_model_attempt
            ),
            egress_host_classes=payload.egress_host_classes,
            egress_correlation_digest=payload.egress_correlation_digest,
            egress_window_started_at=payload.egress_window_started_at,
            egress_window_ended_at=payload.egress_window_ended_at,
            egress_outside_gate_window=payload.egress_outside_gate_window,
            output_digest=payload.output_digest,
            workspace_mutation_receipt_digest=payload.workspace_mutation_receipt_digest,
            rollback_receipt_digest=payload.rollback_receipt_digest,
            sandbox_path_digest=payload.sandbox_path_digest,
            source_ledger_digest=payload.source_ledger_digest,
            final_projection_digest=payload.final_projection_digest,
            research_evidence_status=payload.research_evidence_status,
            citation_evidence_status=payload.citation_evidence_status,
            verifier_evidence_status=payload.verifier_evidence_status,
            final_projection_evidence_status=payload.final_projection_evidence_status,
            source_inspected_event_count=payload.source_inspected_event_count,
            rule_check_event_count=payload.rule_check_event_count,
            unsupported_claim_omitted_count=payload.unsupported_claim_omitted_count,
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
                },
            },
        )

    @app.post("/v1/internal/gate1a/selected-attempt-preflight")
    async def gate1a_selected_attempt_preflight(
        request: Request,
        payload: Gate1ASelectedAttemptPreflightPayload,
    ) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="chat_route_disabled",
                runtime=runtime,
            )
        route_config = _route_config(runtime)
        if not route_config.enabled:
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="canary_gate_disabled",
                runtime=runtime,
            )
        gate_error = _canary_gate_error(runtime, route_config)
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
        budgets = shadow_config.generation_config.approved_budgets
        preflight = shadow_config.counter_store.preflight_gate1a_selected_attempt(
            request_digest=payload.request_digest,
            selected_bot_digest=_sha256_digest(runtime.config.bot_id),
            trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
            environment=route_config.environment,
            max_daily_generation_runs=budgets.max_daily_generation_runs,
            max_daily_generation_cost_usd=budgets.max_daily_generation_cost_usd,
            max_concurrent_generation_runs=budgets.max_concurrent_generation_runs,
            max_pending_generation_runs=budgets.max_pending_generation_runs,
            cost_cap_usd=budgets.max_cost_usd,
            fallback_receipt_path_available=payload.fallback_receipt_path_available,
        )
        return JSONResponse(
            status_code=200 if preflight.status == "ready" else 409,
            content={
                **preflight.model_dump(by_alias=True, mode="json"),
                "diagnosticOnly": True,
                "localOnly": True,
                "responseAuthority": "typescript",
            },
        )


async def run_gate5b_user_visible_chat_response(
    runtime: OpenMagiRuntime,
    payload: object,
    *,
    request: Request,
) -> JSONResponse:
    """Run the selected Gate5B user-visible chat path for HTTP adapters.

    This preserves the existing completions-route boundary in one place so
    additive surfaces such as ``/v1/chat/stream`` can reuse the same selected
    canary gates, ToolHost attachment, evidence, counters, and fallback
    diagnostics without minting a second runtime path.
    """
    route_config = _route_config(runtime)
    if not route_config.enabled:
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason="canary_gate_disabled",
            runtime=runtime,
        )
    gate_error = _canary_gate_error(runtime, route_config)
    if gate_error is not None:
        status_code = 409 if gate_error == "invalid_authority" else 503
        return _fallback_response(
            status_code=status_code,
            status=gate_error,
            reason=_reason_for_gate_error(gate_error),
            runtime=runtime,
        )
    gate1a_bundle = _gate1a_readonly_tool_bundle(runtime, route_config)
    memory_mode = _memory_mode_from_header(
        request.headers.get("x-core-agent-memory-mode")
    )
    gate5b_full_bundle = _gate5b_full_toolhost_bundle(
        runtime, route_config, memory_mode=memory_mode
    )
    tool_bundle = (
        gate5b_full_bundle
        if gate5b_full_bundle.status == "ready"
        else gate1a_bundle
    )
    # Bundles are created per-request; the gate5b host may lazily spawn
    # language-server subprocesses on the first code-file write. Tear them down
    # at the end of the request so we never leak FDs/processes/memory across
    # requests in a long-lived worker pod. Fail-open: shutdown errors never
    # affect the response.
    try:
        if research_first_selected_canary_active(payload):
            try:
                research_first = build_research_first_selected_response(
                    payload,
                    bot_id=runtime.config.bot_id,
                    user_id=runtime.config.user_id,
                    environment=route_config.environment,
                    now_ms=int(time.time() * 1000),
                    request_digest=request.headers.get("x-gate5b-canary-request-digest"),
                )
                shadow_config = _shadow_generation_route_config(runtime)
                if shadow_config.counter_store is None:
                    return _fallback_response(
                        status_code=503,
                        status="python_error",
                        reason="counter_store_unavailable",
                        runtime=runtime,
                    )
                source_ledger = research_first.metadata.get("sourceLedger")
                source_ledger_digest = (
                    source_ledger.get("ledgerDigest")
                    if isinstance(source_ledger, Mapping)
                    else None
                )
                if not isinstance(source_ledger_digest, str):
                    raise ValueError("research-first source ledger digest missing")
                counter_state = (
                    shadow_config.counter_store.record_gate8_research_first_canary_evidence(
                        request_digest=str(research_first.metadata["requestDigest"]),
                        selected_bot_digest=_sha256_digest(runtime.config.bot_id),
                        trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
                        environment=route_config.environment,
                        source_ledger_digest=source_ledger_digest,
                        output_digest=research_first.final_gate_result.final_answer_digest,
                    )
                )
            except (KeyError, OSError, ValidationError, ValueError, TypeError):
                return _fallback_response(
                    status_code=422,
                    status="python_error",
                    reason="research_first_projection_failed",
                    runtime=runtime,
                )
            return _python_ready_response(
                runtime=runtime,
                content=research_first.content,
                event_count=research_first.event_count,
                adk_invoked=False,
                runner_attempted=False,
                model_call_attempted=False,
                mocked_runner_invoked=False,
                counter_state=counter_state,
                counter_status="research_first_completed",
                public_events=research_first.public_events,
                research_first_metadata=research_first.metadata,
            )
        if route_config.mocked_runner is not None:
            return _run_mocked_chat_runner(runtime, route_config, payload, tool_bundle)
        return await _run_live_chat_runner(
            runtime,
            route_config,
            payload,
            request=request,
            gate1a_bundle=tool_bundle,
        )
    finally:
        try:
            gate5b_full_bundle.host.shutdown()
        except Exception:  # noqa: BLE001 — teardown must never break a response
            pass


def _route_config(runtime: OpenMagiRuntime) -> Gate5BUserVisibleChatRouteConfig:
    config = getattr(runtime, "gate5b_user_visible_chat_route_config", None)
    if isinstance(config, Gate5BUserVisibleChatRouteConfig):
        return config
    return Gate5BUserVisibleChatRouteConfig()


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


def _gate1a_config(runtime: OpenMagiRuntime) -> Gate1AReadOnlyToolConfig:
    config = getattr(runtime, "gate1a_readonly_tools_config", None)
    if isinstance(config, Gate1AReadOnlyToolConfig):
        return config
    return Gate1AReadOnlyToolConfig()


def _gate1a_readonly_tool_bundle(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> Gate1AReadOnlyToolBundle:
    return build_gate1a_readonly_tool_bundle(
        config=_gate1a_config(runtime),
        scope={
            "selectedBotDigest": _sha256_digest(runtime.config.bot_id),
            "selectedOwnerDigest": _sha256_digest(runtime.config.user_id),
            "environment": route_config.environment or "local",
        },
        workspace_root=_gate1a_workspace_root(),
    )


def _gate5b_full_toolhost_config(runtime: OpenMagiRuntime) -> Gate5BFullToolHostConfig:
    config = getattr(runtime, "gate5b_full_toolhost_config", None)
    if isinstance(config, Gate5BFullToolHostConfig):
        return config
    return Gate5BFullToolHostConfig()


def _gate5b_full_toolhost_bundle(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    *,
    memory_mode: "MemoryMode | str" = "normal",
) -> Gate5BFullToolBundle:
    return build_gate5b_full_toolhost_bundle(
        config=_gate5b_full_toolhost_config(runtime),
        scope={
            "selectedBotDigest": _sha256_digest(runtime.config.bot_id),
            "selectedOwnerDigest": _sha256_digest(runtime.config.user_id),
            "environment": route_config.environment or "local",
        },
        workspace_root=_gate5b_full_toolhost_workspace_root(),
        tool_registry=runtime.tool_registry,
        memory_mode=memory_mode,
    )


def _gate5b_full_toolhost_workspace_root() -> Path:
    configured = os.environ.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _gate1a_workspace_root() -> Path:
    configured = os.environ.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _shadow_generation_route_config(
    runtime: OpenMagiRuntime,
) -> Gate5B4C3ShadowGenerationRouteConfig:
    config = getattr(runtime, "gate5b4c3_shadow_generation_route_config", None)
    if isinstance(config, Gate5B4C3ShadowGenerationRouteConfig):
        return config
    return Gate5B4C3ShadowGenerationRouteConfig()


def _run_mocked_chat_runner(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    payload: object,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> JSONResponse:
    del gate1a_bundle
    try:
        runner_request = build_gate5b_user_visible_canary_runner_request(
            payload if isinstance(payload, Mapping) else {},
            context_continuity=_context_continuity_chat_diagnostic(runtime),
        )
        result = route_config.mocked_runner(runner_request)
        if not isinstance(result, Mapping):
            raise ValueError("mocked runner result must be a mapping")
        content = sanitize_gate5b_model_visible_identity_text(str(result.get("content") or ""))
        event_count = int(result.get("eventCount") or 0)
    except TimeoutError:
        return _fallback_response(
            status_code=504,
            status="timeout",
            reason="mocked_runner_timeout",
            runtime=runtime,
        )
    except (Exception, ValidationError, ValueError, TypeError):
        return _fallback_response(
            status_code=502,
            status="python_error",
            reason="mocked_runner_error",
            runtime=runtime,
        )
    return _python_ready_response(
        runtime=runtime,
        content=content,
        event_count=event_count,
        adk_invoked=False,
        runner_attempted=False,
        model_call_attempted=False,
        mocked_runner_invoked=True,
    )


def _build_egress_evidence_view(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
):
    """Project the LIVE turn evidence into a PR1 ``SessionEvidenceView``.

    Reuses the PR1 view models. The only real per-turn evidence reachable at
    egress is on the gate5b full toolhost:
      - ``host.read_ledger`` (a real ``ReadLedger``) -> files_read.
      - ``host.counter.receipts`` (tool receipts)    -> tool_calls.
    There is no live ``EvidenceLedger`` at this seam (the toolhost records reads
    in the ReadLedger and tool outcomes as receipts, not as EvidenceLedger
    entries), so files_read are projected directly from the ReadLedger exactly
    as PR2's ``_empty_view_with_optional_reads`` does, and tool_calls from the
    receipts. Phases/verdicts stay empty (no live producer). Pure / read-only;
    never raises.
    """
    from magi_agent.introspection.projection import (
        FileReadView,
        SessionEvidenceView,
        SessionScopeView,
        ToolCallView,
    )

    host = getattr(gate1a_bundle, "host", None)
    read_ledger = getattr(host, "read_ledger", None)
    receipts = tuple(getattr(getattr(host, "counter", None), "receipts", ()) or ())

    files_read: list[FileReadView] = []
    turns: list[str] = []
    # No real session id is threaded to this seam (the builder only receives the
    # tool bundle, not the chat payload). PR2's projection filters the ReadLedger
    # by a known session id; here we derive it from the FIRST read entry and skip
    # any later entries from a different session, keeping the view session-scoped
    # consistent with PR2. If there are no reads the placeholder is retained.
    session_id = "live-egress-session"
    if read_ledger is not None:
        for entry in read_ledger.iter_entries():
            if files_read:
                # Session pinned to the first entry — skip cross-session entries.
                if entry.session_id != session_id:
                    continue
            else:
                session_id = entry.session_id
            turns.append(entry.turn_id)
            files_read.append(
                FileReadView(
                    path=entry.path,
                    sha256=entry.digest,
                    turnId=entry.turn_id,
                    bytes=entry.size_bytes,
                )
            )

    # NOTE: tool_calls here are sourced from gate5b ``host.counter.receipts`` (the
    # egress-time producer), whereas PR2's introspection tool sources tool_calls
    # from EvidenceLedger records. The two producers can diverge for the same turn
    # (different status vocabularies / coverage). Unifying onto a single tool-call
    # source is a documented follow-up; not done here.
    # The receipts carry no per-entry session/turn id at this seam, so the pinned
    # session's placeholder turn id is used for all of them.
    tool_calls = tuple(
        ToolCallView(
            name=receipt.tool_name,
            status=receipt.status,
            turnId="live-egress-turn",
        )
        for receipt in receipts
    )
    return SessionEvidenceView(
        scope=SessionScopeView(
            sessionId=session_id,
            turnsCovered=tuple(dict.fromkeys(turns)),
        ),
        filesRead=tuple(files_read),
        toolCalls=tool_calls,
        phases=(),
        verdicts=(),
    )


async def _maybe_run_egress_critic_gate(
    *,
    payload: object,
    draft_text: str,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> EgressVerifierStatus | None:
    """Run the egress critic gate when the flag is ON. Fail-open; never raises.

    Returns the ``verifier_evidence_status`` value (or ``None`` for no signal /
    not fact-critical / fail-open error). When the flag is OFF this is never
    called, so the egress path is byte-identical to before.
    """
    try:
        from magi_agent.introspection.egress_gate import run_egress_critic_check

        user_query = (
            _extract_last_user_text(payload) if isinstance(payload, Mapping) else ""
        )
        view = _build_egress_evidence_view(gate1a_bundle)
        model_factory = _egress_critic_model_factory(payload)

        evidence_records: list[dict[str, object]] = []
        result = await run_egress_critic_check(
            draft_text=draft_text or "",
            user_query=user_query or "",
            view=view,
            model_factory=model_factory,
            evidence_sink=evidence_records.append,
        )
        for record in evidence_records:
            _log_egress_critic_evidence(record)
        return result.status
    except Exception:  # noqa: BLE001 — egress gate must NEVER break the response
        return None


# Haiku-class fast-model override for the egress critic / fact-critical
# classifier (analogous to ``MAGI_SMART_APPROVE_MODEL`` for SmartApprove). When
# unset the critic uses the runtime's configured provider model.
_ENV_EGRESS_CRITIC_MODEL = "MAGI_EGRESS_CRITIC_MODEL"


def _egress_critic_model_factory(payload: object) -> Callable[[], object] | None:
    """Resolve the critic model factory.

    Resolution order:
      1. Test injection — ``payload["_egressCriticModelFactory"]`` (a private,
         test-only key ignored by the rest of the pipeline) ALWAYS wins so tests
         stay hermetic and never touch a real provider.
      2. Production — build a real Haiku-class model from the runtime's provider
         configuration, reusing the SAME mechanism SmartApprove's
         ``ReadOnlyClassifier`` uses (``resolve_provider_config`` ->
         ``_build_litellm_for_config``). The fast model is overridable via the
         ``MAGI_EGRESS_CRITIC_MODEL`` env var.

    Fail-open is sacrosanct: if no provider config / key can be resolved, or the
    litellm dependency is unavailable, this returns ``None`` and the gate stays
    dormant (status ``None``) — never erroring into the response. Enabling the
    flag without a configured model is therefore always safe.
    """
    if isinstance(payload, Mapping):
        factory = payload.get("_egressCriticModelFactory")
        if callable(factory):
            return factory  # type: ignore[return-value]
    return _production_egress_critic_model_factory()


# Sensible Haiku-class fallback used ONLY if the resolved provider config cannot
# yield its own default model string. Keeps the egress critic explicitly resolved
# rather than ever inheriting SmartApprove's pinned env model.
_EGRESS_CRITIC_DEFAULT_MODEL = "anthropic/claude-haiku-4-5"


def _production_egress_critic_model_factory() -> Callable[[], object] | None:
    """Build a provider-backed critic model factory, or ``None`` (fail open).

    Reuses the exact resolution path of the SmartApprove read-only classifier:
    ``resolve_provider_config()`` discovers the active provider/key from the same
    ``~/.magi/config.toml`` + env sources the runner uses, and
    ``_build_litellm_for_config()`` constructs the ADK ``LiteLlm`` model.

    Model resolution order (resolved EXPLICITLY here so the egress critic never
    silently inherits ``MAGI_SMART_APPROVE_MODEL``):
      1. ``MAGI_EGRESS_CRITIC_MODEL`` env var (Haiku-class fast override), else
      2. the resolved provider config's OWN default model
         (``ProviderConfig.litellm_model``), else
      3. a fixed sensible Haiku-class default (``_EGRESS_CRITIC_DEFAULT_MODEL``).

    A concrete ``model_override`` string is ALWAYS passed into
    ``_build_litellm_for_config`` so SmartApprove's env override is never
    consulted for the egress critic (no cross-coupling). SmartApprove's own
    resolution is unchanged.
    """
    try:
        from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

        provider_config = resolve_provider_config()
    except Exception:  # noqa: BLE001 — fail open (no provider config -> dormant)
        return None

    if provider_config is None:
        # No provider / key configured -> gate stays dormant (fail open).
        return None

    # Explicit resolution: egress env -> provider default -> fixed Haiku default.
    model_override = os.environ.get(_ENV_EGRESS_CRITIC_MODEL, "").strip()
    if not model_override:
        provider_default = getattr(provider_config, "litellm_model", None)
        model_override = (provider_default or "").strip() or _EGRESS_CRITIC_DEFAULT_MODEL

    def _factory() -> object:
        from magi_agent.cli.readonly_classifier import (  # noqa: PLC0415
            _build_litellm_for_config,
        )

        # Pass a concrete model string so the SmartApprove env override
        # (MAGI_SMART_APPROVE_MODEL) is NEVER consulted for the egress critic.
        return _build_litellm_for_config(provider_config, model_override=model_override)

    return _factory


def _log_egress_critic_evidence(record: Mapping[str, object]) -> None:
    """Best-effort structured log of one egress-critic evidence record."""
    try:
        import logging  # noqa: PLC0415

        logging.getLogger("magi_agent.introspection.egress_gate").info(
            "egress_critic_evidence %s",
            json.dumps(
                _safe_egress_critic_evidence_log_record(record),
                ensure_ascii=False,
                default=str,
            ),
        )
    except Exception:  # noqa: BLE001
        pass


def _safe_egress_critic_evidence_log_record(
    record: Mapping[str, object],
) -> dict[str, object]:
    safe_record = dict(record)
    reason = safe_record.get("reason")
    if isinstance(reason, str) and reason and not _SAFE_LABEL_RE.match(reason):
        try:
            from magi_agent.introspection.reason_safety import (  # noqa: PLC0415
                safe_model_reason,
            )

            safe_reason = safe_model_reason(reason, label="egress_reason")
        except Exception:  # noqa: BLE001
            safe_record["reason"] = "egress_reason"
            return safe_record
        safe_record["reason"] = safe_reason.label
        if safe_reason.digest is not None:
            safe_record.setdefault("reason_digest", safe_reason.digest)
        if safe_reason.preview is not None:
            safe_record.setdefault("reason_preview", safe_reason.preview)
    return safe_record


async def _run_live_chat_runner(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    payload: object,
    *,
    request: Request,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> JSONResponse:
    shadow_config = _shadow_generation_route_config(runtime)
    generation_config = shadow_config.generation_config
    if not shadow_config.live_runner_boundary_enabled:
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="live_runner_gate_disabled",
            runtime=runtime,
        )
    if shadow_config.counter_store is None:
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="counter_store_unavailable",
            runtime=runtime,
        )
    try:
        generation = _build_user_visible_generation_request(
            runtime=runtime,
            route_config=route_config,
            generation_config=generation_config,
            payload=payload,
            trace_id=request.headers.get("x-magi-trace-id"),
            canary_request_digest=request.headers.get("x-gate5b-canary-request-digest"),
            gate1a_bundle=gate1a_bundle,
        )
    except (ValidationError, ValueError, TypeError):
        return _fallback_response(
            status_code=422,
            status="python_error",
            reason="invalid_generation_payload",
            runtime=runtime,
        )

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        generation,
        config=generation_config,
    )
    if not diagnostic.accepted:
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason=diagnostic.reason,
            runtime=runtime,
        )
    reservation = shadow_config.counter_store.reserve(
        request_digest=generation.request_id_digest,
        shadow_generation_id=generation.shadow_generation_id,
        selected_bot_digest=generation.selection.bot_id_digest,
        trusted_owner_user_id_digest=generation.selection.owner_user_id_digest,
        environment=generation.selection.environment,
        max_daily_generation_runs=generation.budgets.max_daily_generation_runs,
        max_daily_generation_cost_usd=generation.budgets.max_daily_generation_cost_usd,
        max_concurrent_generation_runs=generation.budgets.max_concurrent_generation_runs,
        max_pending_generation_runs=generation.budgets.max_pending_generation_runs,
        cost_cap_usd=generation.budgets.max_cost_usd,
        cost_owner_waiver=generation_config.cost_owner_waiver,
    )
    if reservation.status != "reserved":
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason=f"counter_{reservation.reason}",
            runtime=runtime,
            counter_state=reservation.counter_state,
            counter_status=reservation.status,
        )
    model_attempt_digest = _model_attempt_digest(
        request_digest=generation.request_id_digest,
        provider=generation.model_routing.provider_label,
        model=generation.model_routing.model_label,
        model_call_attempted=True,
    )
    gate8_ready = _gate8_selected_authority_metadata(runtime) is not None
    gate1a_egress_context, gate1a_egress_proxy_url = (
        _build_gate1a_egress_correlation_context(
            runtime=runtime,
            request_digest=generation.request_id_digest,
            model_attempt_digest=model_attempt_digest,
            gate1a_bundle=gate1a_bundle,
            gate8_ready=gate8_ready,
        )
    )
    model_call_window_start = _utc_now_iso()
    try:
        boundary_result = await run_gate5b4c3_live_runner_boundary_async(
            generation,
            config=generation_config,
            adk_primitives_loader=route_config.adk_primitives_loader,
            adk_tools=gate1a_bundle.tools if gate1a_bundle.status == "ready" else (),
            gate1a_egress_correlation_context=gate1a_egress_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        )
        model_call_window_end = _utc_now_iso()
        report_digest = _sha256_digest(
            "|".join(
                (
                    generation.request_id_digest,
                    boundary_result.status,
                    str(boundary_result.event_count),
                )
            )
        )
    except asyncio.CancelledError:
        counter_state = shadow_config.counter_store.finish(
            reservation,
            status="client_aborted",
            reason="client_aborted",
        )
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="client_aborted",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="client_aborted",
        )
    except TimeoutError:
        runner_error_diagnostic = _chat_runner_error_diagnostic(
            runtime=runtime,
            generation=generation,
            gate1a_bundle=gate1a_bundle,
            stage="runner_execution",
            reason_code="runner_timeout",
            exception_class="TimeoutError",
            exception_category="runner_timeout",
            gate1a_egress_context=gate1a_egress_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        )
        counter_state = _finish_counter_error(
            shadow_config,
            reservation,
            "runner_timeout",
            runner_error_diagnostic=runner_error_diagnostic,
        )
        return _fallback_response(
            status_code=504,
            status="timeout",
            reason="runner_timeout",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="error",
            runner_error_diagnostic=runner_error_diagnostic,
        )
    except Exception as exc:
        runner_error_diagnostic = _chat_runner_error_diagnostic(
            runtime=runtime,
            generation=generation,
            gate1a_bundle=gate1a_bundle,
            stage="unexpected_exception",
            reason_code="runner_boundary_exception",
            exception_class=type(exc).__name__,
            exception_category="unexpected_exception",
            gate1a_egress_context=gate1a_egress_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        )
        counter_state = _finish_counter_error(
            shadow_config,
            reservation,
            "runner_error",
            runner_error_diagnostic=runner_error_diagnostic,
        )
        return _fallback_response(
            status_code=502,
            status="python_error",
            reason="runner_error",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="error",
            runner_error_diagnostic=runner_error_diagnostic,
        )
    if (
        boundary_result.status == "completed"
        and boundary_result.output_text_internal
        and await _client_disconnected(request, route_config)
    ):
        counter_state = shadow_config.counter_store.finish(
            reservation,
            status="completed_after_client_timeout",
            reason="client_aborted_after_runner",
            report_digest=report_digest,
        )
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="client_aborted_after_runner",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="completed_after_client_timeout",
            adk_invoked=boundary_result.adk_invoked,
        )
    runner_error_diagnostic = _boundary_runner_error_diagnostic(
        runtime=runtime,
        boundary_result=boundary_result,
    )
    runner_output_missing = (
        boundary_result.status == "completed"
        and not boundary_result.output_text_internal
    )
    runner_incomplete_reason = (
        None
        if runner_output_missing
        else _runner_incomplete_output_reason(boundary_result.output_text_internal)
    )
    counter_status = (
        "runner_completed"
        if (
            boundary_result.status == "completed"
            and not runner_output_missing
            and runner_incomplete_reason is None
        )
        else "error"
    )
    counter_reason = (
        "runner_output_missing"
        if runner_output_missing
        else runner_incomplete_reason or boundary_result.reason
    )
    counter_state = shadow_config.counter_store.finish(
        reservation,
        status=counter_status,
        reason=counter_reason,
        report_digest=report_digest,
        runner_error_diagnostic=runner_error_diagnostic,
    )
    if (
        boundary_result.status != "completed"
        or runner_output_missing
        or runner_incomplete_reason is not None
    ):
        return _fallback_response(
            status_code=502,
            status="python_error",
            reason=counter_reason,
            runtime=runtime,
            counter_state=counter_state,
            counter_status=counter_status,
            adk_invoked=boundary_result.adk_invoked,
            runner_error_diagnostic=runner_error_diagnostic,
        )
    model_attempt_digest = (
        model_attempt_digest
        if boundary_result.model_call_via_adk_runner_attempted
        else None
    )
    observed_egress_evidence = _collect_gate1a_observed_egress_evidence(
        runtime=runtime,
        request_digest=generation.request_id_digest,
        model_attempt_digest=model_attempt_digest,
        gate1a_bundle=gate1a_bundle,
        gate8_ready=gate8_ready,
        model_call_attempted=boundary_result.model_call_via_adk_runner_attempted,
        observed_window_start=model_call_window_start,
        observed_window_end=model_call_window_end,
    )
    if (
        gate8_ready
        and boundary_result.model_call_via_adk_runner_attempted
        and observed_egress_evidence is None
    ):
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="missing_observed_egress_evidence",
            runtime=runtime,
            counter_state=counter_state,
            counter_status=counter_status,
            adk_invoked=boundary_result.adk_invoked,
        )
    # Egress critic gate (default-OFF). When the flag is OFF this block is
    # skipped entirely so the response is byte-identical to before. When ON, for
    # fact-critical turns it grounds the draft against the real evidence view and
    # sets ``verifierEvidenceStatus`` on the payload. Fail-open: never blocks.
    verifier_evidence_status: EgressVerifierStatus | None = None
    if is_egress_gate_enabled():
        verifier_evidence_status = await _maybe_run_egress_critic_gate(
            payload=payload,
            draft_text=boundary_result.output_text_internal or "",
            gate1a_bundle=gate1a_bundle,
        )
    return _python_ready_response(
        runtime=runtime,
        content=sanitize_gate5b_model_visible_identity_text(
            _bounded_public_text(boundary_result.output_text_internal)
        ),
        event_count=boundary_result.event_count,
        adk_invoked=boundary_result.adk_invoked,
        runner_attempted=boundary_result.runner_attempted,
        model_call_attempted=boundary_result.model_call_via_adk_runner_attempted,
        mocked_runner_invoked=False,
        provider=boundary_result.selected_provider,
        model=boundary_result.selected_model,
        counter_state=counter_state,
        counter_status="runner_completed",
        gate1a_bundle=gate1a_bundle,
        model_attempt_digest=model_attempt_digest,
        observed_egress_evidence=observed_egress_evidence,
        public_events=_gate5b_full_toolhost_public_events(gate1a_bundle),
        first_party_harness_metadata=_first_party_harness_metadata(
            payload=payload,
            gate1a_bundle=gate1a_bundle,
        ),
        verifier_evidence_status=verifier_evidence_status,
    )


def _gate5b_full_toolhost_public_events(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> tuple[Mapping[str, object], ...]:
    if not _route_tool_bundle_full(gate1a_bundle):
        return ()
    turn_id = "turn-gate5b-full-toolhost"
    events: list[Mapping[str, object]] = [
        turn_phase_event(turn_id=turn_id, phase="executing"),
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "started",
            "label": "Running Python ADK",
            "detail": "Selected first-party toolhost active",
        },
    ]
    receipts = getattr(gate1a_bundle.host.counter, "receipts", ())
    for index, receipt in enumerate(receipts[:8], start=1):
        tool_id = _gate5b_full_toolhost_tool_event_id(receipt, index)
        tool_name = str(getattr(receipt, "tool_name", "") or "Tool")
        events.append(tool_start_event(tool_id=tool_id, name=tool_name))
        events.append(
            tool_progress_event(
                tool_id=tool_id,
                label=tool_name,
                status="complete",
                message="Tool receipt recorded",
            )
        )
        events.append(
            tool_end_event(
                tool_id=tool_id,
                status="ok" if getattr(receipt, "status", "") == "ok" else "error",
                output_preview=(
                    f"bytes={getattr(receipt, 'output_byte_count', 0)} "
                    f"result={getattr(receipt, 'bounded_output_digest', '')}"
                ),
                receipt_refs=(f"receipt:{getattr(receipt, 'bounded_output_digest', '')}",),
            )
        )
    events.append(turn_phase_event(turn_id=turn_id, phase="committed"))
    return tuple(events[:25])


def _gate5b_full_toolhost_tool_event_id(receipt: object, index: int) -> str:
    digest = str(getattr(receipt, "tool_call_digest", "") or "")
    if digest.startswith("sha256:") and len(digest) >= 19:
        return f"tu_{digest[7:19]}"
    return f"tu_{index}"


def _finish_counter_error(
    route_config: Gate5B4C3ShadowGenerationRouteConfig,
    reservation: Gate5B4C3ShadowCounterReservation,
    reason: str,
    *,
    runner_error_diagnostic: Mapping[str, object] | None = None,
) -> object:
    return route_config.counter_store.finish(
        reservation,
        status="error",
        reason=reason,
        runner_error_diagnostic=runner_error_diagnostic,
    )


def _boundary_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    boundary_result: object,
) -> dict[str, object] | None:
    diagnostic = getattr(boundary_result, "runner_error_diagnostic", None)
    if diagnostic is None:
        return None
    if hasattr(diagnostic, "model_dump"):
        payload = diagnostic.model_dump(by_alias=True, mode="json", warnings=False)
    elif isinstance(diagnostic, Mapping):
        payload = dict(diagnostic)
    else:
        return None
    return _augment_runner_error_diagnostic(runtime=runtime, payload=payload)


def _chat_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    generation: Gate5B4C3ShadowGenerationRequest,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
    stage: str,
    reason_code: str,
    exception_class: str | None,
    exception_category: str | None,
    gate1a_egress_context: Gate1AEgressCorrelationContext | None,
    gate1a_egress_proxy_url: str | None,
) -> dict[str, object]:
    correlation_ready = (
        gate1a_egress_context is not None
        and bool(str(gate1a_egress_proxy_url or "").strip())
    )
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    payload: dict[str, object] = {
        "schemaVersion": "gate5b4c3.runnerErrorDiagnostic.v1",
        "stage": _safe_label_or_default(stage, "unexpected_exception"),
        "reasonCode": _safe_label_or_default(reason_code, "runner_error"),
        "requestDigest": generation.request_id_digest,
        "traceIdDigest": generation.trace_id_digest,
        "routeMode": _safe_label_or_default(generation.mode, "unknown"),
        "gateMode": _route_tool_bundle_mode(gate1a_bundle),
        "toolsPolicy": _safe_label_or_default(
            generation.recipe_profile.tools_policy,
            "unknown",
        ),
        "routingSource": _safe_label_or_default(
            generation.model_routing.routing_source,
            "unknown",
        ),
        "correlationMode": "proxy_connect_headers" if correlation_ready else "none",
        "activeToolNames": _public_safe_tool_names(gate1a_bundle.exposed_tool_names),
        "adkInvoked": False,
        "runnerAttempted": False,
        "modelCallAttempted": False,
        "toolsEnabled": not generation.policy.tools_disabled,
        "toolHostDispatchAllowed": generation.policy.tool_host_dispatch_allowed,
        "adkPrimitivesLoaderConfigured": True,
        "gate1aEgressCorrelationContextPresent": gate1a_egress_context is not None,
        "gate1aProxyUrlConfigured": bool(str(gate1a_egress_proxy_url or "").strip()),
        "egressCorrelationHeadersConfigured": correlation_ready,
    }
    if exception_class is not None:
        payload["exceptionClass"] = _safe_label_or_default(exception_class, "Exception")
    if exception_category is not None:
        payload["exceptionCategory"] = _safe_label_or_default(
            exception_category,
            "unexpected_exception",
        )
    if gate1a_egress_context is not None:
        payload["correlationDigest"] = gate1a_egress_context.correlation_digest
        if gate1a_egress_context.model_attempt_digest is not None:
            payload["modelAttemptDigest"] = gate1a_egress_context.model_attempt_digest
    return _augment_runner_error_diagnostic(runtime=runtime, payload=payload) or payload


def _augment_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    safe_payload = _public_safe_runner_error_diagnostic(payload)
    if safe_payload is None:
        return None
    runtime_version = _safe_label_or_none(getattr(runtime.config.build, "version", None))
    build_sha = _safe_label_or_none(getattr(runtime.config.build, "build_sha", None))
    if runtime_version is not None:
        safe_payload["runtimeVersion"] = runtime_version
    if build_sha is not None:
        safe_payload["buildSha"] = build_sha
    provider = get_observed_egress_evidence_provider(runtime)
    egress_diagnostic = observed_egress_diagnostics(provider)
    safe_payload["observedEgressEvidenceAvailable"] = bool(
        egress_diagnostic["observedEgressEvidenceAvailable"]
    )
    safe_payload["gate1aEgressEvidenceReady"] = bool(
        egress_diagnostic["gate1aEgressEvidenceReady"]
    )
    return safe_payload


def _context_continuity_chat_diagnostic(
    runtime: OpenMagiRuntime,
) -> dict[str, object] | None:
    continuity = getattr(runtime.config, "context_continuity", None)
    if continuity is None or getattr(continuity, "enabled", False) is not True:
        return None
    metadata = getattr(continuity, "health_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    return _public_safe_context_continuity_metadata(
        {
            "schemaVersion": "pregate8.contextContinuityChatDiagnostic.v1",
            "source": "server_runtime_config",
            "phase": "pre_gate8",
            "localOnly": True,
            "diagnosticOnly": True,
            "responseAuthority": "none",
            "clientMessagesTrustedForContinuity": False,
            **metadata,
        }
    )


def _public_safe_context_continuity_metadata(
    value: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    safe: dict[str, object] = {
        "schemaVersion": "pregate8.contextContinuityChatDiagnostic.v1",
        "source": "server_runtime_config",
        "phase": "pre_gate8",
        "localOnly": True,
        "diagnosticOnly": True,
        "responseAuthority": "none",
        "clientMessagesTrustedForContinuity": False,
    }
    bool_fields = (
        "continuityEnabled",
        "continuityCanaryReady",
        "compactionApplied",
        "projectionDigestPresent",
        "modelVisibleDigestPresent",
        "sourceTranscriptHeadDigestPresent",
        "canaryEvidenceVerified",
        "productionAuthorityAllowed",
        "transcriptWriteAllowed",
        "sseWriteAllowed",
        "dbWriteAllowed",
    )
    int_fields = ("importedEventCount", "rejectedEntryCount")
    label_fields = ("mode", "canaryStatus", "fallbackStatus")
    for field in bool_fields:
        safe[field] = value.get(field) is True
    for field in int_fields:
        safe[field] = max(0, _int_for_public_metadata(value.get(field)))
    for field in label_fields:
        safe[field] = _safe_label_or_default(value.get(field), "missing")
    reason_codes = value.get("reasonCodes")
    safe["reasonCodes"] = (
        _public_safe_context_reason_codes(reason_codes)
        if isinstance(reason_codes, (list, tuple))
        else []
    )
    safe["productionAuthorityAllowed"] = False
    safe["transcriptWriteAllowed"] = False
    safe["sseWriteAllowed"] = False
    safe["dbWriteAllowed"] = False
    return safe


def _public_safe_context_reason_codes(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_codes: list[str] = []
    for value in values:
        safe_value = _safe_label_or_none(value)
        if safe_value is None or safe_value in safe_codes:
            continue
        if _CONTEXT_REASON_CODE_FORBIDDEN_RE.search(safe_value):
            continue
        safe_codes.append(safe_value)
        if len(safe_codes) >= 16:
            break
    return safe_codes


def _int_for_public_metadata(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _public_safe_runner_error_diagnostic(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    safe_payload: dict[str, object] = {}
    string_fields = {
        "schemaVersion",
        "stage",
        "reasonCode",
        "exceptionClass",
        "exceptionCategory",
        "routeMode",
        "gateMode",
        "toolsPolicy",
        "routingSource",
        "correlationMode",
    }
    digest_fields = {
        "requestDigest",
        "traceIdDigest",
        "modelAttemptDigest",
        "correlationDigest",
    }
    bool_fields = {
        "adkInvoked",
        "runnerAttempted",
        "modelCallAttempted",
        "toolsEnabled",
        "toolHostDispatchAllowed",
        "adkPrimitivesLoaderConfigured",
        "gate1aEgressCorrelationContextPresent",
        "gate1aProxyUrlConfigured",
        "egressCorrelationHeadersConfigured",
    }
    for key, value in payload.items():
        if key in string_fields and isinstance(value, str):
            safe_value = _safe_label_or_none(value)
            if safe_value is not None:
                safe_payload[key] = safe_value
            continue
        if key in digest_fields and isinstance(value, str) and _is_sha256_digest(value):
            safe_payload[key] = value
            continue
        if key in bool_fields and isinstance(value, bool):
            safe_payload[key] = value
            continue
        if key == "activeToolNames" and isinstance(value, (list, tuple)):
            tool_names = _public_safe_tool_names(value)
            if tool_names:
                safe_payload[key] = tool_names
            continue
        if key == "errorPreview" and isinstance(value, str):
            error_preview = _public_safe_error_preview_or_none(value)
            if error_preview is not None:
                safe_payload[key] = error_preview
            continue
        if key == "tracebackMarkers" and isinstance(value, (list, tuple)):
            traceback_markers = _public_safe_traceback_markers(value)
            if traceback_markers:
                safe_payload[key] = traceback_markers
    if "stage" not in safe_payload or "reasonCode" not in safe_payload:
        return None
    safe_payload["schemaVersion"] = "gate5b4c3.runnerErrorDiagnostic.v1"
    return safe_payload


def _public_safe_tool_names(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_names: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _SAFE_LABEL_RE.match(text) and text not in safe_names:
            safe_names.append(text)
    return safe_names


def _public_safe_error_preview_or_none(value: object) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > 256:
        return None
    if _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE.search(text):
        return None
    return text


def _public_safe_traceback_markers(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_markers: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _SAFE_LABEL_RE.match(text) and text not in safe_markers:
            safe_markers.append(text)
        if len(safe_markers) >= 12:
            break
    return safe_markers


def _fallback_only_scope_error(
    *,
    payload: Gate5BUserVisibleDeliveryReceiptPayload,
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> str | None:
    if (
        payload.gate not in _FALLBACK_RECEIPT_SCOPE_GATES
        or payload.delivery_status != "fallback_served"
        or not payload.python_attempted
        or payload.python_counter_record_present
    ):
        return None
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
    return None


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


def _safe_label_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_LABEL_RE.match(text) else None


def _safe_label_or_default(value: object, fallback: str) -> str:
    return _safe_label_or_none(value) or fallback


async def _client_disconnected(
    request: Request,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> bool:
    if route_config.client_disconnected_probe is not None:
        value = route_config.client_disconnected_probe(request)
        if inspect.isawaitable(value):
            value = await value
        return bool(value)
    try:
        return bool(await request.is_disconnected())
    except Exception:
        return False


def _build_user_visible_generation_request(
    *,
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    generation_config: Gate5B4C3ShadowGenerationConfig,
    payload: object,
    trace_id: str | None,
    canary_request_digest: str | None = None,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
) -> Gate5B4C3ShadowGenerationRequest:
    if not isinstance(payload, Mapping):
        raise ValueError("chat payload must be an object")
    user_text = _extract_last_user_text(payload)
    if not user_text:
        raise ValueError("chat payload must contain a user message")
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    history_messages = _build_gate5b_sanitized_recent_history(
        payload,
        max_messages=(
            generation_config.approved_budgets.max_sanitized_history_messages
            if full_toolhost_ready
            else 0
        ),
    )
    sanitized_text = _build_gate5b_model_visible_current_turn_text(
        user_text,
        payload=None if history_messages else payload,
    )
    input_digest = _sha256_digest(sanitized_text)
    request_seed = "|".join(
        (
            runtime.config.bot_id,
            runtime.config.user_id,
            route_config.environment,
            input_digest,
            trace_id or "",
        )
    )
    request_digest = (
        canary_request_digest
        if _is_sha256_digest(canary_request_digest)
        else _sha256_digest(request_seed)
    )
    provider_label = _single_config_value(generation_config.allowed_provider_labels)
    model_label = _single_config_value(generation_config.allowed_model_labels)
    credential_ref = _single_config_value(generation_config.allowed_shadow_credential_refs)
    router_digest = _sha256_digest(f"{provider_label}:{model_label}:{request_digest}")
    profile_digest = _sha256_digest("gate5b-user-visible-canary-profile-v1")
    tools_policy = (
        "selected_full_toolhost"
        if full_toolhost_ready
        else "shadow_readonly" if tool_bundle_ready else "disabled"
    )
    source_authority = (
        "bounded_sanitized_recent_history"
        if history_messages
        else "current_turn_only"
    )
    now_ms = int(time.time() * 1000)
    turn_payload: dict[str, object] = {
        "turnId": f"turn_{input_digest.removeprefix('sha256:')[:16]}",
        "turnDigest": _sha256_digest(request_seed + ":turn"),
        "sanitizedCurrentTurnText": sanitized_text,
        "sanitizedInputTextDigest": input_digest,
        "channelName": "app_channel",
        "tsResponseCorrelationId": f"ts_{request_digest.removeprefix('sha256:')[:16]}",
    }
    if history_messages:
        turn_payload["sanitizedRecentHistory"] = history_messages
    redacted_byte_count = len(sanitized_text.encode("utf-8")) + sum(
        len(str(item["sanitizedText"]).encode("utf-8"))
        for item in history_messages
    )
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        {
            "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
            "mode": "shadow_generation_diagnostic",
            "responseAuthority": "typescript",
            "shadowGenerationId": f"uv_canary_{request_digest.removeprefix('sha256:')[:24]}",
            "requestIdDigest": request_digest,
            "traceIdDigest": _sha256_digest(trace_id or request_digest),
            "createdAt": now_ms,
            "selection": {
                "botIdDigest": _sha256_digest(runtime.config.bot_id),
                "ownerUserIdDigest": _sha256_digest(runtime.config.user_id),
                "environment": route_config.environment,
                "selectedTarget": "gate5b_selected_bot",
            },
            "turn": turn_payload,
            "modelRouting": {
                "routingSource": "per_turn_injected",
                "providerLabel": provider_label,
                "modelLabel": model_label,
                "routerDecisionDigest": router_digest,
                "routingProfileDigest": profile_digest,
                "shadowCredentialRef": credential_ref,
                "credentialRefSource": "server_config",
                "maxOutputTokens": generation_config.approved_budgets.max_output_tokens,
            },
            "recipeProfile": {
                "recipeId": "gate5b-user-visible-canary",
                "recipeVersion": "v1",
                "profileId": "base-python-text-canary",
                "profileVersion": "v1",
                "runtimeEngine": "adk-python",
                "toolsPolicy": tools_policy,
                "memoryMode": "disabled",
                "sourceAuthority": source_authority,
            },
            "policy": {
                "typeScriptResponseAuthority": True,
                "pythonDiagnosticOnly": True,
                "outputIsolation": "local_diagnostic_only",
                "toolsDisabled": not tool_bundle_ready,
                "toolHostDispatchAllowed": tool_bundle_ready,
                "memoryProviderCallsAllowed": False,
                "memoryWritesAllowed": False,
                "promptMemoryInjectionAllowed": False,
                "workspaceMutationAllowed": False,
                "childExecutionAllowed": False,
                "missionRuntimeAllowed": False,
                "evidenceBlockModeAllowed": False,
            },
            "budgets": generation_config.approved_budgets.model_dump(
                by_alias=True,
                mode="python",
            ),
            "redaction": {
                "sanitizerId": "gate5b-user-visible-canary",
                "sanitizerVersion": "v1",
                "policyId": (
                    "bounded-sanitized-recent-history"
                    if history_messages
                    else "current-turn-only"
                ),
                "status": "passed",
                "redactedAt": now_ms,
                "redactedByteCount": redacted_byte_count,
                "forbiddenFieldScan": "passed",
                "sanitizedPayloadDigest": input_digest,
            },
        }
    )


def _extract_last_user_text(payload: Mapping[str, object]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for block in content:
                if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
            return "\n".join(chunks)
    return ""


def _single_config_value(values: tuple[str, ...]) -> str:
    if len(values) != 1:
        raise ValueError("Gate 5B user-visible canary requires one configured value")
    return values[0]


def _build_gate5b_sanitized_recent_history(
    payload: Mapping[str, object],
    *,
    max_messages: int,
) -> tuple[dict[str, str], ...]:
    if max_messages <= 0:
        return ()
    projected: list[dict[str, str]] = list(_app_channel_history_messages(payload))
    source_messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if not isinstance(source_messages, list):
        return _dedupe_latest_history(projected, max_messages=max_messages)
    last_user_index: int | None = None
    for index, item in enumerate(source_messages):
        if isinstance(item, Mapping) and item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        for item in source_messages[:last_user_index]:
            if not isinstance(item, Mapping):
                continue
            message = _sanitized_history_message(
                role=item.get("role"),
                content=item.get("content"),
            )
            if message is not None:
                projected.append(message)
    return _dedupe_latest_history(projected, max_messages=max_messages)


def _app_channel_history_messages(
    payload: Mapping[str, object],
) -> tuple[dict[str, str], ...]:
    channel_history = payload.get("channelHistory")
    if not isinstance(channel_history, Mapping):
        return ()
    if channel_history.get("schema") != _APP_CHANNEL_HISTORY_SCHEMA:
        return ()
    raw_messages = channel_history.get("messages")
    if not isinstance(raw_messages, list):
        return ()
    projected: list[dict[str, str]] = []
    for item in raw_messages:
        if not isinstance(item, Mapping):
            continue
        message = _sanitized_history_message(
            role=item.get("role"),
            content=item.get("content"),
        )
        if message is not None:
            projected.append(message)
    return tuple(projected)


def _sanitized_history_message(
    *,
    role: object,
    content: object,
) -> dict[str, str] | None:
    role_text = str(role or "").strip().lower()
    if role_text == "system":
        role_text = "assistant"
    if role_text not in {"user", "assistant"}:
        return None
    text = _message_content_to_text(content)
    text = _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE.sub("[redacted]", text)
    text = sanitize_gate5b_model_visible_identity_text(text)
    bounded = text[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
    if not bounded:
        return None
    return {
        "role": role_text,
        "sanitizedText": bounded,
        "sanitizedTextDigest": _sha256_digest(bounded),
    }


def _dedupe_latest_history(
    messages: Sequence[Mapping[str, str]],
    *,
    max_messages: int,
) -> tuple[dict[str, str], ...]:
    if max_messages <= 0:
        return ()
    seen: set[tuple[str, str]] = set()
    selected_reversed: list[dict[str, str]] = []
    for item in reversed(messages):
        role = str(item.get("role") or "")
        digest = str(item.get("sanitizedTextDigest") or "")
        key = (role, digest)
        if key in seen:
            continue
        seen.add(key)
        selected_reversed.append(dict(item))
        if len(selected_reversed) >= max_messages:
            break
    return tuple(reversed(selected_reversed))


def _bounded_public_text(value: str, *, max_chars: int = 8192) -> str:
    return value[:max_chars]


def _runner_incomplete_output_reason(value: object) -> str | None:
    text = _bounded_public_text(str(value or ""), max_chars=4096).strip()
    if not text:
        return None
    if _INCOMPLETE_RUNNER_OUTPUT_RE.search(text):
        return "runner_incomplete_output"
    return None


def build_public_identity_policy() -> dict[str, str]:
    return dict(_PUBLIC_IDENTITY_POLICY)


def sanitize_gate5b_model_visible_identity_text(value: object) -> str:
    text, _signals = _normalize_gate5b_model_visible_identity_text(value)
    return text


def build_gate5b_user_visible_canary_runner_request(
    payload: Mapping[str, Any],
    *,
    context_continuity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    messages: list[dict[str, str]] = []
    signals: list[str] = []

    source_messages = payload.get("messages") if isinstance(payload, Mapping) else None
    projected_messages: list[dict[str, str]] = []
    for item in _app_channel_history_messages(payload):
        projected_messages.append(
            {"role": item["role"], "content": item["sanitizedText"]}
        )
    if isinstance(source_messages, list):
        for item in source_messages:
            if not isinstance(item, Mapping):
                continue
            role = _safe_chat_role(item.get("role"))
            content, content_signals = _normalize_gate5b_model_visible_identity_text(
                _message_content_to_text(item.get("content"))
            )
            _extend_unique(signals, content_signals)
            bounded = content[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
            if bounded:
                projected_messages.append({"role": role, "content": bounded})
        messages.extend(_latest_model_visible_messages(projected_messages))

    workspace_identity_context: list[str] = []
    for key in (
        "workspaceIdentityText",
        "workspace_identity_text",
        "identityText",
        "identity_text",
    ):
        if key not in payload:
            continue
        content, content_signals = _normalize_gate5b_model_visible_identity_text(payload[key])
        _extend_unique(signals, content_signals)
        bounded = content[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
        if bounded:
            workspace_identity_context.append(bounded)

    request: dict[str, object] = {
        "schemaVersion": "gate5b.userVisibleCanaryRunnerRequest.v1",
        "publicIdentity": build_public_identity_policy(),
        "messages": tuple(messages),
        "workspaceIdentityContext": tuple(workspace_identity_context),
        "legacyIdentitySignals": tuple(signals),
        "limits": {
            "maxModelVisibleContextChars": _MODEL_VISIBLE_CONTEXT_MAX_CHARS,
        },
    }
    safe_continuity = _public_safe_context_continuity_metadata(context_continuity)
    if safe_continuity is not None:
        request["contextContinuity"] = safe_continuity
    return request


def _latest_model_visible_messages(
    messages: Sequence[Mapping[str, str]],
    *,
    limit: int = 16,
) -> tuple[dict[str, str], ...]:
    if len(messages) <= limit:
        return tuple(dict(item) for item in messages)
    system_messages = [dict(item) for item in messages if item.get("role") == "system"]
    conversation_messages = [
        dict(item) for item in messages if item.get("role") != "system"
    ]
    selected_system = system_messages[: min(len(system_messages), 2)]
    remaining = max(1, limit - len(selected_system))
    return tuple([*selected_system, *conversation_messages[-remaining:]])


def _build_gate5b_model_visible_current_turn_text(
    user_text: str,
    *,
    payload: Mapping[str, object] | None = None,
) -> str:
    identity = _PUBLIC_IDENTITY_POLICY["modelVisibleSystemContext"]
    sanitized_user_text = sanitize_gate5b_model_visible_identity_text(
        _bounded_public_text(user_text, max_chars=_MODEL_VISIBLE_CONTEXT_MAX_CHARS)
    )
    projected_messages: list[dict[str, str]] = []
    source_messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if isinstance(source_messages, list):
        for item in source_messages:
            if not isinstance(item, Mapping):
                continue
            content = sanitize_gate5b_model_visible_identity_text(
                _message_content_to_text(item.get("content"))
            )
            bounded = content[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
            if bounded:
                projected_messages.append(
                    {"role": _safe_chat_role(item.get("role")), "content": bounded}
                )
    visible_messages = _latest_model_visible_messages(projected_messages)
    if visible_messages:
        conversation = "\n".join(
            f"{item['role']}: {item['content']}" for item in visible_messages
        )
        text = (
            f"{identity}\n\n"
            f"Recent visible conversation:\n{conversation}\n\n"
            f"Current user message:\n{sanitized_user_text}"
        )
    else:
        text = f"{identity}\n\nUser message:\n{sanitized_user_text}"
    return _bounded_public_text(text, max_chars=_MODEL_VISIBLE_CONTEXT_MAX_CHARS)


def _message_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _normalize_gate5b_model_visible_identity_text(value: object) -> tuple[str, tuple[str, ...]]:
    text = _message_content_to_text(value)
    signals: list[str] = []
    for pattern, replacement, signal in _LEGACY_IDENTITY_PATTERNS:
        if not pattern.search(text):
            continue
        text = pattern.sub(replacement, text)
        if signal not in signals:
            signals.append(signal)
    return text, tuple(signals)


def _safe_chat_role(value: object) -> str:
    role = str(value or "user").strip().lower()
    if role in {"system", "user", "assistant"}:
        return role
    return "user"


def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _canary_gate_error(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> str | None:
    authority = runtime.config.authority
    if route_config.kill_switch_enabled is not False:
        return "python_disabled"
    if route_config.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "python_disabled"
    if route_config.selected_owner_user_id_digest != _sha256_digest(runtime.config.user_id):
        return "python_disabled"
    if not route_config.environment or route_config.environment not in route_config.environment_allowlist:
        return "python_disabled"
    if (
        runtime.config.gate8_readiness.enabled
        and _gate8_selected_authority_metadata(runtime) is None
    ):
        return "python_disabled"
    if (
        authority.user_visible_output_allowed is not True
        or authority.canary_routing_allowed is not True
    ):
        return "invalid_authority"
    for key in _FALSE_RUNTIME_AUTHORITY_KEYS:
        attr = _camel_to_snake(key).replace("writes", "write")
        if getattr(authority, attr) is not False:
            return "invalid_authority"
    return None


def gate5b_user_visible_chat_gate_active(runtime: OpenMagiRuntime) -> bool:
    route_config = _route_config(runtime)
    return route_config.enabled is True and _canary_gate_error(runtime, route_config) is None


def _model_attempt_digest(
    *,
    request_digest: str,
    provider: str,
    model: str,
    model_call_attempted: bool,
) -> str | None:
    if not model_call_attempted:
        return None
    return _sha256_digest(f"{request_digest}:{provider}:{model}:attempt:1")


def _collect_gate1a_observed_egress_evidence(
    *,
    runtime: OpenMagiRuntime,
    request_digest: str,
    model_attempt_digest: str | None,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
    gate8_ready: bool = False,
    model_call_attempted: bool,
    observed_window_start: str | None = None,
    observed_window_end: str | None = None,
) -> ObservedEgressEvidence | None:
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    if not (tool_bundle_ready or gate8_ready) or not model_call_attempted:
        return None
    provider = get_observed_egress_evidence_provider(runtime)
    return provider.collect(
        request_digest=request_digest,
        model_attempt_digest=model_attempt_digest,
        observed_window_start=observed_window_start,
        observed_window_end=observed_window_end,
    )


def _first_party_recipe_pack_ids_from_payload(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        return ()
    availability = payload.get("botScopedRecipeAvailability")
    if not isinstance(availability, Mapping):
        availability = payload.get("bot_scoped_recipe_availability")
    if not isinstance(availability, Mapping):
        return ()
    values = availability.get("availableRecipePackIds")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        values = availability.get("available_recipe_pack_ids")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    allowed = set(_FIRST_PARTY_HARNESS_RECIPE_PACK_IDS)
    selected: list[str] = []
    for value in values:
        if isinstance(value, str) and value in allowed and value not in selected:
            selected.append(value)
    return tuple(selected)


def _first_party_harness_families(pack_ids: Sequence[str]) -> tuple[str, ...]:
    ids = set(pack_ids)
    families: list[str] = []
    checks = (
        ("methodology", {"openmagi.agent-methodology", "openmagi.superpowers-compat"}),
        ("research", {"openmagi.research", "openmagi.web-acquisition"}),
        ("coding", {"openmagi.dev-coding"}),
        (
            "general_automation",
            {
                "openmagi.office-automation",
                "openmagi.spreadsheet-automation",
                "openmagi.document-review",
                "openmagi.lightweight-scripting",
            },
        ),
        ("memory", {"openmagi.memory-agentmemory"}),
        ("scheduler", {"openmagi.missions", "openmagi.scheduled-work"}),
        ("channel_delivery", {"openmagi.channel-delivery", "openmagi.artifact-delivery"}),
        ("browser", {"openmagi.browser-automation", "openmagi.web-acquisition"}),
    )
    for family, required in checks:
        if ids.intersection(required):
            families.append(family)
    return tuple(families)


def _bounded_tuple(values: Sequence[str], *, limit: int = 64) -> list[str]:
    return [str(value) for value in values[:limit]]


def _first_party_harness_metadata(
    *,
    payload: object,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> dict[str, object] | None:
    pack_ids = _first_party_recipe_pack_ids_from_payload(payload)
    if not pack_ids:
        return None
    registry = PackRegistry.with_first_party_packs()
    try:
        snapshot = AgentRecipeCompiler(registry).compile(
            ProfileResolutionRequest(
                recipePackConfig={"packs": {"enable": pack_ids}},
            )
        )
        plan = RecipeMaterializer.with_reliability_defaults().materialize(
            snapshot,
            modelProvider="google",
            modelLabel="gemini-3.5-flash",
        )
    except (ValidationError, ValueError, TypeError):
        return {
            "schemaVersion": "openmagi.firstPartyHarnessAdmission.v1",
            "status": "blocked",
            "reason": "first_party_harness_materialization_failed",
            "requestedPackCount": len(pack_ids),
            "selectedPackIds": [],
            "harnessFamilies": [],
        }
    toolhost_mode = "disabled"
    if _route_tool_bundle_full(gate1a_bundle):
        toolhost_mode = "selected_full_toolhost"
    elif _route_tool_bundle_readonly(gate1a_bundle):
        toolhost_mode = "shadow_readonly"
    active_toolhost = {
        "mode": toolhost_mode,
        "allowedToolNames": _route_tool_bundle_names(gate1a_bundle),
        "productionAttached": False,
    }
    return {
        "schemaVersion": "openmagi.firstPartyHarnessAdmission.v1",
        "status": "ready",
        "recipeSnapshotId": plan.recipe_snapshot_id,
        "selectedPackIds": list(plan.selected_pack_ids),
        "harnessFamilies": list(_first_party_harness_families(plan.selected_pack_ids)),
        "providerIntents": _bounded_tuple(plan.provider_intents),
        "toolIntents": _bounded_tuple(plan.tool_intents),
        "channelIntents": _bounded_tuple(plan.channel_intents),
        "artifactIntents": _bounded_tuple(plan.artifact_intents),
        "schedulerIntents": _bounded_tuple(plan.scheduler_intents),
        "evidenceRequirements": _bounded_tuple(plan.evidence_requirements),
        "approvalGates": _bounded_tuple(plan.approval_gates),
        "killSwitchRefs": _bounded_tuple(plan.kill_switch_refs),
        "rollbackRefs": _bounded_tuple(plan.rollback_refs),
        "liveAttachmentRefs": list(plan.live_attachment_refs),
        "attachmentFlags": {
            str(key): bool(value)
            for key, value in plan.attachment_flags.items()
        },
        "activeSelectedToolhost": active_toolhost,
    }


def _build_gate1a_egress_correlation_context(
    *,
    runtime: OpenMagiRuntime,
    request_digest: str,
    model_attempt_digest: str | None,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
    gate8_ready: bool = False,
) -> tuple[Gate1AEgressCorrelationContext | None, str | None]:
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    if not (tool_bundle_ready or gate8_ready):
        return None, None
    if not _is_sha256_digest(request_digest) or not _is_sha256_digest(
        model_attempt_digest
    ):
        return None, None
    provider = get_observed_egress_evidence_provider(runtime)
    if (
        getattr(provider, "gate1a_egress_evidence_ready", False) is not True
        or getattr(provider, "evidence_source", "") != GATE1A_EGRESS_TELEMETRY_SOURCE
        or getattr(provider, "correlation_mode", "") != GATE1A_EGRESS_CORRELATION_MODE
    ):
        return None, None
    proxy_url = getattr(provider, "gate1a_proxy_url", None)
    if not isinstance(proxy_url, str) or not proxy_url.strip():
        return None, None
    try:
        return (
            Gate1AEgressCorrelationContext(
                request_digest=request_digest,
                correlation_digest=request_digest,
                model_attempt_digest=model_attempt_digest,
            ),
            proxy_url.strip(),
        )
    except ValueError:
        return None, None


def _python_ready_response(
    *,
    runtime: OpenMagiRuntime,
    content: str,
    event_count: int,
    adk_invoked: bool,
    runner_attempted: bool,
    model_call_attempted: bool,
    mocked_runner_invoked: bool,
    provider: str | None = None,
    model: str | None = None,
    counter_state: object | None = None,
    counter_status: str = "runner_completed",
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    model_attempt_digest: str | None = None,
    observed_egress_evidence: ObservedEgressEvidence | None = None,
    public_events: Sequence[Mapping[str, object]] = (),
    research_first_metadata: Mapping[str, object] | None = None,
    first_party_harness_metadata: Mapping[str, object] | None = None,
    verifier_evidence_status: EgressVerifierStatus | None = None,
) -> JSONResponse:
    active_tools = _route_tool_bundle_names(gate1a_bundle)
    gate8_metadata = _gate8_selected_authority_metadata(runtime)
    gate8_ready = bool(gate8_metadata and gate8_metadata.get("readinessReady") is True)
    body: dict[str, object] = {
        "schemaVersion": "gate5b.userVisibleChatCompletion.v1",
        "status": "python_ready",
        "fallbackStatus": "none",
        "responseAuthority": "python",
        "runtime": runtime.config.runtime,
        "runtimeEngine": runtime.config.runtime_engine,
        "authority": _python_canary_authority(gate1a_bundle, gate8_ready=gate8_ready),
        "safety": _surface_safety(gate1a_bundle, gate8_ready=gate8_ready),
        "adk": {
            "available": runtime.adk_boundary.available,
            "invoked": adk_invoked,
        },
        "activeTools": active_tools,
        "runnerAttempted": runner_attempted,
        "modelCallAttempted": model_call_attempted,
        "modelAttemptCount": 1 if model_call_attempted else 0,
        "mockedRunnerInvoked": mocked_runner_invoked,
        "eventCount": event_count,
        "publicEvents": [
            dict(event)
            for event in public_events
            if isinstance(event, Mapping)
        ],
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    if counter_state is not None and hasattr(counter_state, "model_dump"):
        body["counter"] = {
            "status": counter_status,
            "state": counter_state.model_dump(by_alias=True, mode="json"),
        }
    if _route_tool_bundle_ready(gate1a_bundle):
        body["tooling"] = _route_tooling_metadata(gate1a_bundle)
    if model_call_attempted and (
        _route_tool_bundle_ready(gate1a_bundle) or gate8_ready
    ):
        body.update(
            _gate1a_observed_egress_metadata(
                observed_egress_evidence=observed_egress_evidence,
                model_attempt_digest=model_attempt_digest,
            )
        )
    if gate8_ready and gate8_metadata is not None:
        body["gate"] = "gate8_selected_python_authority"
        body["gate8Readiness"] = gate8_metadata
    if research_first_metadata is not None:
        body["researchFirst"] = dict(research_first_metadata)
    if first_party_harness_metadata is not None:
        body["firstPartyHarness"] = dict(first_party_harness_metadata)
    # Egress critic gate signal (default-OFF). Only added to the body when the
    # gate ran AND produced a non-None status, so the off-state body is
    # byte-identical to before.
    if verifier_evidence_status is not None:
        body["verifierEvidenceStatus"] = verifier_evidence_status
    return JSONResponse(status_code=200, content=body)


def _fallback_response(
    *,
    status_code: int,
    status: str,
    reason: str,
    runtime: OpenMagiRuntime,
    counter_state: object | None = None,
    counter_status: str = "closed",
    adk_invoked: bool = False,
    runner_error_diagnostic: Mapping[str, object] | None = None,
    extra_content: Mapping[str, object] | None = None,
) -> JSONResponse:
    content = (
        {
            "status": status,
            "fallbackStatus": "fallback_to_typescript",
            "responseAuthority": "typescript",
            "reason": reason,
            "runtime": runtime.config.runtime,
            "runtimeEngine": runtime.config.runtime_engine,
            "adk": {
                "available": runtime.adk_boundary.available,
                "invoked": adk_invoked,
            },
        }
        if status != "python_disabled"
        else {
            "status": status,
            "fallbackStatus": "fallback_to_typescript",
            "responseAuthority": "typescript",
            "reason": reason,
            "runtime": runtime.config.runtime,
            "runtimeEngine": runtime.config.runtime_engine,
        }
    )
    if extra_content is not None:
        content.update(dict(extra_content))
    if counter_state is not None and hasattr(counter_state, "model_dump"):
        content["counter"] = {
            "status": counter_status,
            "state": counter_state.model_dump(by_alias=True, mode="json"),
        }
    if runner_error_diagnostic:
        content["runnerErrorDiagnostic"] = dict(runner_error_diagnostic)
    context_continuity = _context_continuity_chat_diagnostic(runtime)
    if context_continuity is not None:
        content["contextContinuity"] = context_continuity
    return JSONResponse(status_code=status_code, content=content)



def _reason_for_gate_error(status: str) -> str:
    if status == "invalid_authority":
        return "authority_gate_not_satisfied"
    return "canary_gate_disabled"


def _python_canary_authority(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    *,
    gate8_ready: bool = False,
) -> dict[str, bool]:
    gate1a_ready = _route_tool_bundle_readonly(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    authority = {
        "userVisibleOutputAllowed": True,
        "canaryRoutingAllowed": True,
        **{key: False for key in _FALSE_RESPONSE_AUTHORITY_KEYS},
    }
    if gate1a_ready:
        authority["readOnlyToolDispatchAllowed"] = True
    if full_toolhost_ready:
        authority["toolDispatchAllowed"] = True
        authority["selectedWorkspaceMutationAllowed"] = True
        authority["productionWorkspaceMutationAllowed"] = False
        authority["bashCommandAllowed"] = "Bash" in _route_tool_bundle_names(gate1a_bundle)
    if gate8_ready:
        authority["readOnlyToolDispatchAllowed"] = False
        authority["backgroundTaskAllowed"] = False
        authority["selfImprovementAllowed"] = False
    return authority


def _surface_safety(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    *,
    gate8_ready: bool = False,
) -> dict[str, object]:
    gate1a_ready = _route_tool_bundle_readonly(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    safety: dict[str, object] = {
        "toolsActive": False,
        "memoryProviderActive": False,
        "browserActive": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "telegramDeliveryAllowed": False,
        "artifactChannelDeliveryAllowed": False,
        "evidenceBlockModeAllowed": False,
        "productionTranscriptWritesAllowed": False,
        "productionSseWritesAllowed": False,
        "productionDbWritesAllowed": False,
    }
    if gate1a_ready:
        safety.update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": True,
                "toolHostMode": "shadow_readonly",
                "allowedReadOnlyTools": list(gate1a_bundle.exposed_tool_names),
                "writeMutationAllowed": False,
            }
        )
    if full_toolhost_ready:
        safety.update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": False,
                "toolHostMode": "selected_full_toolhost",
                "allowedToolNames": _route_tool_bundle_names(gate1a_bundle),
                "selectedWorkspaceMutationAllowed": True,
                "productionWorkspaceMutationAllowed": False,
                "writeMutationAllowed": True,
                "bashCommandAllowed": "Bash" in _route_tool_bundle_names(gate1a_bundle),
            }
        )
    if gate8_ready:
        safety.update(
            {
                "readOnlyToolsActive": False,
                "toolHostMode": "disabled",
                "schedulerMutationAllowed": False,
                "backgroundTaskAllowed": False,
                "selfImprovementAllowed": False,
            }
        )
    return safety


def _disabled_surface_safety() -> dict[str, bool]:
    return {
        key: value
        for key, value in _surface_safety().items()
        if isinstance(value, bool)
    }


def _gate8_selected_authority_metadata(
    runtime: OpenMagiRuntime,
) -> dict[str, object] | None:
    gate8 = gate8_readiness_health_metadata(
        runtime.config.gate8_readiness,
        runtime.config.context_continuity,
        bot_id=runtime.config.bot_id,
        user_id=runtime.config.user_id,
        observed_egress=observed_egress_diagnostics(
            get_observed_egress_evidence_provider(runtime)
        ),
    )
    return gate8 if gate8.get("readinessReady") is True else None


def _route_tool_bundle_ready(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> bool:
    return bundle is not None and bundle.status == "ready"


def _route_tool_bundle_full(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> bool:
    return isinstance(bundle, Gate5BFullToolBundle) and bundle.status == "ready"


def _route_tool_bundle_readonly(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> bool:
    return isinstance(bundle, Gate1AReadOnlyToolBundle) and bundle.status == "ready"


def _route_tool_bundle_names(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> list[str]:
    if not _route_tool_bundle_ready(bundle):
        return []
    return list(bundle.exposed_tool_names)


def _route_tool_bundle_mode(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> str:
    if _route_tool_bundle_full(bundle):
        return "gate5b_selected_full_toolhost"
    if _route_tool_bundle_readonly(bundle):
        return "gate1a_readonly_tools"
    return "no_route_tools"


def _route_tooling_metadata(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> dict[str, object]:
    if isinstance(bundle, Gate5BFullToolBundle):
        return _gate5b_full_tooling_metadata(bundle)
    return _gate1a_tooling_metadata(bundle)


def _gate1a_tooling_metadata(bundle: Gate1AReadOnlyToolBundle) -> dict[str, object]:
    attachment_flags = bundle.attachment_flags.model_dump(by_alias=True, mode="json")
    exposed = set(bundle.exposed_tool_names)
    forbidden = sorted(exposed.intersection(GATE1A_FORBIDDEN_TOOL_NAMES))
    return {
        "schemaVersion": "gate1a.readOnlyTooling.v1",
        "mode": "shadow_readonly",
        "toolsPolicy": "shadow_readonly",
        "allowedToolNames": list(bundle.exposed_tool_names),
        "forbiddenToolsExposed": forbidden,
        "receiptCount": bundle.host.counter.receipt_count,
        "routeAttached": attachment_flags["routeAttached"],
        "productionAttached": attachment_flags["productionAttached"],
        "attachmentFlags": attachment_flags,
        "sourceLedgerProjection": bundle.source_ledger_projection,
        "receiptLimits": {
            "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
            "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
            "maxAggregateOutputBytes": bundle.host.config.max_aggregate_output_bytes,
        },
    }


def _gate5b_full_tooling_metadata(bundle: Gate5BFullToolBundle) -> dict[str, object]:
    attachment_flags = bundle.attachment_flags.model_dump(by_alias=True, mode="json")
    exposed = set(bundle.exposed_tool_names)
    forbidden = sorted(
        name
        for name in exposed
        if name not in set(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    )
    return {
        "schemaVersion": "gate5b.selectedFullToolhost.v1",
        "mode": "selected_full_toolhost",
        "toolsPolicy": "selected_full_toolhost",
        "allowedToolNames": list(bundle.exposed_tool_names),
        "forbiddenToolsExposed": forbidden,
        "receiptCount": bundle.host.counter.receipt_count,
        "routeAttached": attachment_flags["routeAttached"],
        "productionAttached": attachment_flags["productionAttached"],
        "workspaceRootDigest": bundle.workspace_root_digest,
        "attachmentFlags": attachment_flags,
        "receiptLimits": {
            "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
            "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
            "commandTimeoutMs": bundle.host.config.command_timeout_ms,
        },
    }


def _gate1a_observed_egress_metadata(
    *,
    observed_egress_evidence: ObservedEgressEvidence | None,
    model_attempt_digest: str | None,
) -> dict[str, object]:
    if observed_egress_evidence is None:
        metadata: dict[str, object] = {
            "egressEvidenceStatus": "missing_observed_egress_evidence",
        }
        if model_attempt_digest is not None:
            metadata["modelAttemptDigest"] = model_attempt_digest
        return metadata

    evidence = observed_egress_evidence.model_dump(by_alias=True, mode="json")
    provider_request_count = observed_egress_evidence.provider_request_count
    expected_max = (
        _GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT
        * max(provider_request_count, 1)
    )
    metadata = {
        "egressEvidenceStatus": "observed_egress_evidence_present",
        "observedEgressEvidence": evidence,
        "providerRequestCount": provider_request_count,
        "egressTunnelCount": observed_egress_evidence.egress_tunnel_count,
        "egressHostClasses": list(observed_egress_evidence.egress_host_classes),
        "egressDisciplineMode": _GATE1A_EGRESS_DISCIPLINE_MODE,
        "expectedEgressTunnelRange": {"min": 0, "max": expected_max},
        "egressEvidenceSource": observed_egress_evidence.evidence_source,
        "egressEvidenceRedactionStatus": observed_egress_evidence.redaction_status,
        "egressEvidenceDecisionReason": observed_egress_evidence.decision_reason,
        "egressWindowStartedAt": observed_egress_evidence.observed_window_start,
        "egressWindowEndedAt": observed_egress_evidence.observed_window_end,
    }
    correlation_digest = (
        observed_egress_evidence.correlation_digest
        or observed_egress_evidence.request_digest
    )
    if correlation_digest is not None:
        metadata["egressCorrelationDigest"] = correlation_digest
    if observed_egress_evidence.model_attempt_digest is not None:
        metadata["modelAttemptDigest"] = observed_egress_evidence.model_attempt_digest
    elif model_attempt_digest is not None:
        metadata["modelAttemptDigest"] = model_attempt_digest
    return metadata


def _sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _is_sha256_digest(value: object) -> bool:
    return isinstance(value, str) and re.match(r"^sha256:[a-f0-9]{64}$", value) is not None


def _is_true(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_default_true(value: object) -> bool:
    if value is None:
        return True
    normalized = str(value or "").strip().lower()
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    return True


def _int_env(value: object, *, fallback: int) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return fallback


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")


__all__ = [
    "Gate2SandboxWorkspaceCanaryConfig",
    "Gate5BUserVisibleChatRouteConfig",
    "build_gate1a_readonly_tools_config_from_env",
    "build_gate2_sandbox_workspace_canary_config_from_env",
    "build_gate5b_full_toolhost_config_from_env",
    "build_gate5b_user_visible_chat_route_config_from_env",
    "build_gate5b_user_visible_canary_runner_request",
    "build_public_identity_policy",
    "gate5b_user_visible_chat_gate_active",
    "register_chat_routes",
    "run_gate5b_user_visible_chat_response",
    "sanitize_gate5b_model_visible_identity_text",
]
