from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


PriorityAReadinessStatus: TypeAlias = Literal["not_ready"]
PriorityAPathStatus: TypeAlias = Literal["not_ready"]
PriorityAResponseAuthority: TypeAlias = Literal["none"]
PriorityAPathReason: TypeAlias = Literal[
    "disabled_by_default",
    "local_diagnostic_metadata_only",
]
PriorityAPathKey: TypeAlias = Literal[
    "turn_scoped_model_provider_routing",
    "provider_capability_metadata",
    "runner_invocation_metadata_projection",
    "retry_fallback_policy_metadata",
    "empty_response_fallback_metadata",
    "polling_downgrade_restore_metadata",
    "route_cache_metadata",
]
RuntimeHeartbeatReadinessStatus: TypeAlias = Literal["local_fake_ready"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)

_GROUP_A_PATHS: tuple[tuple[PriorityAPathKey, str], ...] = (
    (
        "turn_scoped_model_provider_routing",
        "Turn-scoped model/provider routing compatibility metadata.",
    ),
    (
        "provider_capability_metadata",
        "Provider capability and health posture metadata.",
    ),
    (
        "runner_invocation_metadata_projection",
        "Future ADK Runner invocation metadata projection.",
    ),
    (
        "retry_fallback_policy_metadata",
        "Retry and fallback policy metadata for future selected turns.",
    ),
    (
        "empty_response_fallback_metadata",
        "Empty-response fallback metadata without model execution.",
    ),
    (
        "polling_downgrade_restore_metadata",
        "Provider polling downgrade and restore metadata.",
    ),
    (
        "route_cache_metadata",
        "Route cache source, staleness, and TTL metadata.",
    ),
)

_FUTURE_LIVE_PRIMITIVES = (
    "ADK Runner",
    "ADK Agent",
    "ADK Event",
    "ADK SessionService",
)
_RUNTIME_HEARTBEAT_FALSE_FIELDS = (
    "runtime_heartbeat_enabled",
    "scheduler_attached",
    "production_writes_enabled",
    "traffic_attached",
    "trusted_lease_authority",
    "live_authority",
    "model_call_enabled",
    "provider_call_enabled",
    "tool_execution_enabled",
    "channel_delivery_enabled",
    "workspace_mutation_enabled",
    "memory_write_enabled",
    "runner_invoked",
    "mission_runtime_enabled",
    "public_ui_heartbeat_coupled",
)
_RUNTIME_HEARTBEAT_TRUE_FIELDS = (
    "readiness_ready",
    "local_fake_store_ready",
    "durable_primitives_ready",
    "default_off",
    "contract_only",
)
_RUNTIME_HEARTBEAT_REASON_CODES = (
    "local_fake_runtime_heartbeat_contract_ready",
)


class _PriorityAReadinessModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class PriorityAReadinessConfig(_PriorityAReadinessModel):
    enabled: bool = False
    reason: PriorityAPathReason = "disabled_by_default"


class PriorityAReadinessAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    canary_routing_allowed: Literal[False] = Field(
        default=False,
        alias="canaryRoutingAllowed",
    )
    toolhost_active: Literal[False] = Field(default=False, alias="toolHostActive")
    memory_provider_active: Literal[False] = Field(
        default=False,
        alias="memoryProviderActive",
    )
    transcript_writes_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWritesAllowed",
    )
    sse_writes_allowed: Literal[False] = Field(default=False, alias="sseWritesAllowed")
    channel_writes_allowed: Literal[False] = Field(
        default=False,
        alias="channelWritesAllowed",
    )
    db_writes_allowed: Literal[False] = Field(default=False, alias="dbWritesAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    mission_runtime_allowed: Literal[False] = Field(
        default=False,
        alias="missionRuntimeAllowed",
    )
    artifact_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="artifactDeliveryAllowed",
    )
    evidence_block_mode_allowed: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_flag_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(by_alias=True, mode="python"))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _false_flag_payload(cls)

    @field_serializer(
        "user_visible_output_allowed",
        "canary_routing_allowed",
        "toolhost_active",
        "memory_provider_active",
        "transcript_writes_allowed",
        "sse_writes_allowed",
        "channel_writes_allowed",
        "db_writes_allowed",
        "workspace_mutation_allowed",
        "child_execution_allowed",
        "mission_runtime_allowed",
        "artifact_delivery_allowed",
        "evidence_block_mode_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class PriorityAPathDiagnostic(_PriorityAReadinessModel):
    priority_group: Literal["A"] = Field(default="A", alias="priorityGroup")
    path_key: PriorityAPathKey = Field(alias="pathKey")
    description: str
    enabled: Literal[False] = False
    diagnostic_ready: Literal[False] = Field(default=False, alias="diagnosticReady")
    live_ready: Literal[False] = Field(default=False, alias="liveReady")
    status: PriorityAPathStatus = "not_ready"
    response_authority: PriorityAResponseAuthority = Field(
        default="none",
        alias="responseAuthority",
    )
    reason: PriorityAPathReason

    @model_validator(mode="before")
    @classmethod
    def _force_false_only_path(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["priorityGroup"] = "A"
        data["enabled"] = False
        data["diagnosticReady"] = False
        data["liveReady"] = False
        data["status"] = "not_ready"
        data["responseAuthority"] = "none"
        data.pop("priority_group", None)
        data.pop("diagnostic_ready", None)
        data.pop("live_ready", None)
        data.pop("response_authority", None)
        return data

    @field_serializer("enabled", "diagnostic_ready", "live_ready")
    def _serialize_false(self, _value: object) -> bool:
        return False


class PriorityAReadinessSnapshot(_PriorityAReadinessModel):
    schema_version: Literal["priorityA.localReadiness.v1"] = Field(
        default="priorityA.localReadiness.v1",
        alias="schemaVersion",
    )
    priority_group: Literal["A"] = Field(default="A", alias="priorityGroup")
    enabled: Literal[False] = False
    readiness_status: PriorityAReadinessStatus = Field(
        default="not_ready",
        alias="readinessStatus",
    )
    diagnostic_ready: Literal[False] = Field(default=False, alias="diagnosticReady")
    selected_turn_ready: Literal[False] = Field(default=False, alias="selectedTurnReady")
    replacement_ready: Literal[False] = Field(default=False, alias="replacementReady")
    response_authority: PriorityAResponseAuthority = Field(
        default="none",
        alias="responseAuthority",
    )
    paths: tuple[PriorityAPathDiagnostic, ...]
    authority_flags: PriorityAReadinessAuthorityFlags = Field(
        default_factory=PriorityAReadinessAuthorityFlags,
        alias="authorityFlags",
    )
    future_live_primitives: tuple[str, ...] = Field(alias="futureLivePrimitives")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    production_writes_allowed: Literal[False] = Field(
        default=False,
        alias="productionWritesAllowed",
    )
    fastapi_route_activation_allowed: Literal[False] = Field(
        default=False,
        alias="fastapiRouteActivationAllowed",
    )
    provider_sdk_import_allowed: Literal[False] = Field(
        default=False,
        alias="providerSdkImportAllowed",
    )
    adk_runner_invocation_allowed: Literal[False] = Field(
        default=False,
        alias="adkRunnerInvocationAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_snapshot_false_only(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["schemaVersion"] = "priorityA.localReadiness.v1"
        data["priorityGroup"] = "A"
        data["enabled"] = False
        data["readinessStatus"] = "not_ready"
        data["diagnosticReady"] = False
        data["selectedTurnReady"] = False
        data["replacementReady"] = False
        data["responseAuthority"] = "none"
        data["authorityFlags"] = PriorityAReadinessAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        data["futureLivePrimitives"] = _FUTURE_LIVE_PRIMITIVES
        data["metadataOnly"] = True
        data["productionWritesAllowed"] = False
        data["fastapiRouteActivationAllowed"] = False
        data["providerSdkImportAllowed"] = False
        data["adkRunnerInvocationAllowed"] = False
        data.pop("schema_version", None)
        data.pop("priority_group", None)
        data.pop("readiness_status", None)
        data.pop("diagnostic_ready", None)
        data.pop("selected_turn_ready", None)
        data.pop("replacement_ready", None)
        data.pop("response_authority", None)
        data.pop("authority_flags", None)
        data.pop("future_live_primitives", None)
        data.pop("metadata_only", None)
        data.pop("production_writes_allowed", None)
        data.pop("fastapi_route_activation_allowed", None)
        data.pop("provider_sdk_import_allowed", None)
        data.pop("adk_runner_invocation_allowed", None)
        return data

    @field_serializer(
        "enabled",
        "diagnostic_ready",
        "selected_turn_ready",
        "replacement_ready",
        "production_writes_allowed",
        "fastapi_route_activation_allowed",
        "provider_sdk_import_allowed",
        "adk_runner_invocation_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


def build_priority_a_readiness_snapshot(
    config: PriorityAReadinessConfig | None = None,
) -> PriorityAReadinessSnapshot:
    resolved_config = config or PriorityAReadinessConfig()
    reason: PriorityAPathReason = (
        "local_diagnostic_metadata_only"
        if resolved_config.enabled
        else resolved_config.reason
    )
    paths = tuple(
        PriorityAPathDiagnostic(
            pathKey=path_key,
            description=description,
            reason=reason,
        )
        for path_key, description in _GROUP_A_PATHS
    )
    return PriorityAReadinessSnapshot(
        paths=paths,
        authorityFlags=PriorityAReadinessAuthorityFlags(),
        futureLivePrimitives=_FUTURE_LIVE_PRIMITIVES,
    )


class RuntimeHeartbeatReadinessSnapshot(_PriorityAReadinessModel):
    schema_version: Literal["openmagi.runtime.heartbeat.readiness.v1"] = Field(
        default="openmagi.runtime.heartbeat.readiness.v1",
        alias="schemaVersion",
    )
    status: RuntimeHeartbeatReadinessStatus = "local_fake_ready"
    readiness_ready: Literal[True] = Field(default=True, alias="readinessReady")
    local_fake_store_ready: Literal[True] = Field(
        default=True,
        alias="localFakeStoreReady",
    )
    durable_primitives_ready: Literal[True] = Field(
        default=True,
        alias="durablePrimitivesReady",
    )
    runtime_heartbeat_enabled: Literal[False] = Field(
        default=False,
        alias="runtimeHeartbeatEnabled",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    trusted_lease_authority: Literal[False] = Field(
        default=False,
        alias="trustedLeaseAuthority",
    )
    live_authority: Literal[False] = Field(default=False, alias="liveAuthority")
    model_call_enabled: Literal[False] = Field(
        default=False,
        alias="modelCallEnabled",
    )
    provider_call_enabled: Literal[False] = Field(
        default=False,
        alias="providerCallEnabled",
    )
    tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="toolExecutionEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_write_enabled: Literal[False] = Field(
        default=False,
        alias="memoryWriteEnabled",
    )
    runner_invoked: Literal[False] = Field(default=False, alias="runnerInvoked")
    mission_runtime_enabled: Literal[False] = Field(
        default=False,
        alias="missionRuntimeEnabled",
    )
    public_ui_heartbeat_coupled: Literal[False] = Field(
        default=False,
        alias="publicUiHeartbeatCoupled",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    contract_only: Literal[True] = Field(default=True, alias="contractOnly")
    reason_codes: tuple[
        Literal["local_fake_runtime_heartbeat_contract_ready"],
        ...,
    ] = Field(default=_RUNTIME_HEARTBEAT_REASON_CODES, alias="reasonCodes")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls(**_runtime_heartbeat_readiness_payload(cls, values))

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _force_runtime_heartbeat_readiness_contract(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return _runtime_heartbeat_readiness_payload(cls, {})
        return _runtime_heartbeat_readiness_payload(cls, value)

    @field_serializer(
        "runtime_heartbeat_enabled",
        "scheduler_attached",
        "production_writes_enabled",
        "traffic_attached",
        "trusted_lease_authority",
        "live_authority",
        "model_call_enabled",
        "provider_call_enabled",
        "tool_execution_enabled",
        "channel_delivery_enabled",
        "workspace_mutation_enabled",
        "memory_write_enabled",
        "runner_invoked",
        "mission_runtime_enabled",
        "public_ui_heartbeat_coupled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @field_serializer(
        "readiness_ready",
        "local_fake_store_ready",
        "durable_primitives_ready",
        "default_off",
        "contract_only",
    )
    def _serialize_true(self, _value: object) -> bool:
        return True


def build_runtime_heartbeat_readiness_snapshot() -> RuntimeHeartbeatReadinessSnapshot:
    return RuntimeHeartbeatReadinessSnapshot()


def _false_flag_payload(cls: type[BaseModel]) -> dict[str, bool]:
    return {field.alias or name: False for name, field in cls.model_fields.items()}


def _runtime_heartbeat_readiness_payload(
    cls: type[BaseModel],
    value: Mapping[str, object],
) -> dict[str, object]:
    data = dict(value)
    alias_to_name = {
        field.alias: name
        for name, field in cls.model_fields.items()
        if field.alias is not None
    }
    for alias, field_name in alias_to_name.items():
        if alias in data and field_name not in data:
            data[field_name] = data[alias]
    for field_name in (
        "schema_version",
        "status",
        "reason_codes",
        *_RUNTIME_HEARTBEAT_FALSE_FIELDS,
        *_RUNTIME_HEARTBEAT_TRUE_FIELDS,
    ):
        field = cls.model_fields[field_name]
        if field.alias is not None:
            data.pop(field.alias, None)
        data.pop(field_name, None)
    data["schemaVersion"] = "openmagi.runtime.heartbeat.readiness.v1"
    data["status"] = "local_fake_ready"
    data["reasonCodes"] = _RUNTIME_HEARTBEAT_REASON_CODES
    for field_name in _RUNTIME_HEARTBEAT_FALSE_FIELDS:
        data[cls.model_fields[field_name].alias or field_name] = False
    for field_name in _RUNTIME_HEARTBEAT_TRUE_FIELDS:
        data[cls.model_fields[field_name].alias or field_name] = True
    return data


__all__ = [
    "PriorityAPathDiagnostic",
    "PriorityAPathKey",
    "PriorityAPathReason",
    "PriorityAPathStatus",
    "PriorityAReadinessAuthorityFlags",
    "PriorityAReadinessConfig",
    "PriorityAReadinessSnapshot",
    "PriorityAReadinessStatus",
    "PriorityAResponseAuthority",
    "RuntimeHeartbeatReadinessSnapshot",
    "RuntimeHeartbeatReadinessStatus",
    "build_priority_a_readiness_snapshot",
    "build_runtime_heartbeat_readiness_snapshot",
]
