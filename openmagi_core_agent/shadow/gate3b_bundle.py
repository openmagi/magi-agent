from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_JSON_RECORD_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="allow",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_ALLOWED_GATE3B_LITERALS = frozenset(
    {
        "gate3b.liveDuplicateBundle.v1",
        "selected_bot_post_turn_bundle",
        "typescript-core-agent",
        "typescript-only",
        "live_duplicate_redacted",
        "verified",
        "live_duplicate_validation_metadata",
        "post_turn_redacted_duplicate",
        "recorded_metadata_only",
        "recorded",
        "local_replay",
    }
)
_ATTACHMENT_FALSE_KEYS = frozenset(
    {
        "production_route_attached",
        "production_transcript_attached",
        "production_sse_attached",
        "user_output_attached",
        "telegram_attached",
        "live_tool_attached",
        "live_runner_attached",
        "production_storage_attached",
        "production_queue_attached",
        "evidence_block_attached",
    }
)
_AUTHORITY_FALSE_KEYS = frozenset(
    {
        "can_delay_typescript_response",
        "can_alter_typescript_response",
        "can_block_typescript_response",
        "can_influence_user_output",
        "python_response_authority",
    }
)
_ATTACHMENT_FIELD_NAMES = _ATTACHMENT_FALSE_KEYS
_AUTHORITY_FIELD_NAMES = _AUTHORITY_FALSE_KEYS | {"typescript_response_authority_only"}
_ALLOWED_EXECUTION_DECLARED_SURFACES = frozenset(
    {
        "recorded",
        "recorded_only",
        "recorded_metadata",
        "recorded_metadata_only",
        "declared_metadata",
        "declared_metadata_only",
    }
)
_SAFE_FALSE_KEYS_BY_PARENT = {
    "source_provenance": frozenset(
        {
            "production_path_included",
            "live_traffic_consumed",
        }
    ),
    "recorded_tool_results": frozenset({"dispatched_live"}),
    "evidence_audit_metadata": frozenset({"external_ack_included"}),
    "execution_surface": frozenset(
        {
            "shell_executed",
            "code_executed",
            "live_tool_executed",
            "package_manager_executed",
            "script_executed",
            "command_executed",
            "generated_script_executed",
            "external_side_effects",
            "tool_side_effects",
        }
    ),
}
_FALSE_ONLY_METADATA_KEYS = frozenset(
    {
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "storage_written",
        "queue_enqueued",
        "user_visible_output_attached",
        "public_output_attached",
    }
)
_FALSE_ONLY_COMPACT_KEY_TOKENS = frozenset(
    {
        "liveshadowexecuted",
        "toolsexecuted",
        "shellorcodeexecuted",
        "storagewritten",
        "queueenqueued",
        "uservisibleoutputattached",
        "publicoutputattached",
    }
)
_PRIVATE_KEYS = frozenset(
    {
        "hidden_reasoning",
        "chain_of_thought",
        "private_reasoning",
        "reasoning_trace",
        "private_tool_preview",
        "private_tool_input",
        "private_tool_output",
        "raw_tool_preview",
        "raw_connector_credentials",
        "child_private_records",
        "private_preview",
    }
)
_CREDENTIAL_KEY_PARTS = frozenset(
    {
        "authorization",
        "auth",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
        "apikey",
        "api_key",
        "access_key",
        "access_token",
        "refresh_token",
        "github",
        "telegram_token",
        "raw_auth_headers",
        "connector_credentials",
    }
)
_EXECUTION_CLAIM_KEYS = frozenset(
    {
        "shell_executed",
        "shell_invoked",
        "shell_ran",
        "code_executed",
        "code_invoked",
        "live_tool_executed",
        "live_tool_dispatched",
        "package_manager_executed",
        "package_manager_invoked",
        "package_manager_ran",
        "script_executed",
        "script_dispatched",
        "script_invoked",
        "script_ran",
        "script_run",
        "command_executed",
        "command_dispatched",
        "command_invoked",
        "command_ran",
        "command_run",
        "generated_script_executed",
        "auto_executed",
        "tool_dispatched",
        "tool_invoked",
        "external_side_effects",
        "tool_side_effect",
        "tool_side_effects",
        "dispatched_live",
        "dispatched",
        "invoked",
    }
)
_FORBIDDEN_COMPACT_KEY_TOKENS = frozenset(
    {
        "productiontranscriptwrite",
        "productiontranscriptappend",
        "productionssewrite",
        "productionsseappend",
        "productionroute",
        "productionrouteattached",
        "productionstorage",
        "productionqueue",
        "uservisibleoutput",
        "useroutputattached",
        "telegram",
        "telegramattached",
        "livetool",
        "livetoolattached",
        "livetoolsideeffect",
        "liverunner",
        "liverunnerattached",
        "productionstorageattached",
        "productionqueueattached",
        "evidenceblock",
        "evidenceblockmode",
        "signedack",
        "signedexternalack",
        "rawacknowledgement",
        "customextractor",
        "childexecution",
        "livecaptureincluded",
        "livetrafficconsumed",
        "livecaptureconsumed",
        "rawconnectorcredentialsincluded",
        "canarytrafficenabled",
        "pvcmounted",
        "workspaceattached",
        "workspacemounted",
        "workspacemutation",
        "workspaceadoption",
        "adkrunnerinvoked",
        "adkrunnerattached",
        "backgroundresume",
        "schedulerrun",
        "schedulerresume",
    }
)
_FORBIDDEN_COMPACT_VALUE_TOKENS = frozenset(
    {
        "hiddenreasoning",
        "chainofthought",
        "privatereasoning",
        "reasoningtrace",
        "privatetoolinput",
        "privatetoolpreview",
        "rawtoolpreview",
        "rawconnectorcredentials",
        "signedack",
        "signedexternalack",
        "signedacknowledgementpayload",
        "signedexternalacknowledgementpayload",
        "productiontranscriptwrite",
        "productionsseappend",
        "productionroute",
        "productionrouteattached",
        "productionqueue",
        "productionstorage",
        "telegram",
        "telegramattached",
        "livetool",
        "livetoolattached",
        "liverunner",
        "liverunnerattached",
        "evidenceblock",
        "productionqueueattached",
        "evidenceblockmodeenabled",
        "customextractor",
        "customextractoroutput",
        "childexecution",
        "workspacemutation",
        "workspaceadoption",
        "workspaceadoptionrequested",
        "schedulerrun",
        "backgroundresume",
        "schedulerresume",
        "schedulerresumerequested",
        "livecaptureincluded",
        "livetrafficconsumed",
        "livecaptureconsumed",
        "rawconnectorcredentialsincluded",
        "canarytraffic",
        "canarytrafficenabled",
        "pvcmounted",
        "workspaceattached",
        "workspacemounted",
        "adkrunnerinvoked",
        "adkrunnerattached",
        "shellexecution",
        "shellrun",
        "commandexecution",
        "commandrun",
        "livetoolran",
    }
)
_SECRET_TEXT_RE = re.compile(
    r"(?:"
    r"\bAuthorization\s*:\s*Bearer\s+\S+|"
    r"\bAuthorization\s*:\s*\S+|"
    r"\bBearer\s+\S+|"
    r"\bCookie\s*:\s*\S+|"
    r"\bgh[opusr]_[A-Za-z0-9_]{8,}|"
    r"\bgithub_pat_[A-Za-z0-9_]+|"
    r"(?:^|[\s:=,;'\"`({\[])sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}(?![A-Za-z0-9_-])|"
    r"\b[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_SERVICE_ROLE_KEY|_PASSWORD|_API_KEY)\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"-----BEGIN (?:[A-Z0-9 -]+ )?PRIVATE KEY-----|"
    r"-----BEGIN OPENSSH PRIVATE KEY-----|"
    r"\b\d{5,}:[A-Za-z0-9_-]{10,}|"
    r"\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|refresh[_-]?token|password|secret|token)\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"\b[a-z][a-z0-9+.-]*://\S+|"
    r"\bclawy\.pro\b\S*|"
    r"/(?:data|workspace|mnt|var|private|tmp)\S*|"
    r"\bbot-[A-Za-z0-9_-]+|"
    r"\bpvc\b|"
    r"\bkube(?:let|rnetes)?\b|"
    r"\b(?:postgres(?:ql)?|supabase|s3|gs)://\S+"
    r")",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s('\"`=:;,])(?:/(?!/)\S+|[a-zA-Z]:[\\/]\S*)")
_EXECUTION_TEXT_RE = re.compile(
    r"(?:"
    r"\b(?:shell|code runner|package manager|script|command)\b.{0,32}\bexecut(?:e|ed|ion)\b|"
    r"\bexecut(?:e|ed|ion)\b.{0,32}\b(?:shell|code runner|package manager|script|command)\b|"
    r"\b(?:shell|script|command)\b.{0,32}\bran\b|"
    r"\blive tool\b.{0,32}\b(?:dispatch|execut|side effect)|"
    r"\bexternal side effects?\b"
    r")",
    re.IGNORECASE,
)
_GATE3B_BUNDLE_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|"
    r"(?:^|[\\/])(?:missions?|schedulers?)(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:mission|scheduler)-store(?:[\\/]|$)|"
    r"bot-[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


class _Gate3BModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        dumped = super().model_dump(*args, **kwargs)
        _reject_unsafe_gate3b_value(dumped, _path=_gate3b_model_dump_validation_path(self))
        return dumped

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        dumped_json = super().model_dump_json(*args, **kwargs)
        _reject_unsafe_gate3b_value(
            json.loads(dumped_json),
            _path=_gate3b_model_dump_validation_path(self),
        )
        return dumped_json

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
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


class _FrozenJsonDict(dict[str, object]):
    def __readonly(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("Gate 3B JSON metadata is immutable")

    __setitem__ = __readonly
    __delitem__ = __readonly
    clear = __readonly
    pop = __readonly
    popitem = __readonly
    setdefault = __readonly
    update = __readonly
    __ior__ = __readonly


class Gate3BAttachmentFlags(_Gate3BModel):
    production_route_attached: Literal[False] = Field(
        default=False,
        alias="productionRouteAttached",
    )
    production_transcript_attached: Literal[False] = Field(
        default=False,
        alias="productionTranscriptAttached",
    )
    production_sse_attached: Literal[False] = Field(default=False, alias="productionSseAttached")
    user_output_attached: Literal[False] = Field(default=False, alias="userOutputAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    live_runner_attached: Literal[False] = Field(default=False, alias="liveRunnerAttached")
    production_storage_attached: Literal[False] = Field(
        default=False,
        alias="productionStorageAttached",
    )
    production_queue_attached: Literal[False] = Field(
        default=False,
        alias="productionQueueAttached",
    )
    evidence_block_attached: Literal[False] = Field(default=False, alias="evidenceBlockAttached")

    def __getattribute__(self, name: str) -> object:
        if name in _ATTACHMENT_FIELD_NAMES:
            return False
        return super().__getattribute__(name)

    @model_validator(mode="before")
    @classmethod
    def _reject_non_false_input(cls, value: object) -> object:
        if isinstance(value, Gate3BAttachmentFlags):
            _reject_raw_attachment_flag_state(value)
            return value.model_dump(by_alias=True, mode="python", warnings=False)
        if isinstance(value, Mapping):
            for flag_value in value.values():
                if flag_value is not False:
                    raise ValueError("Gate 3B attachment flags must be false")
        return value

    @model_validator(mode="after")
    def _reject_constructed_true_state(self) -> Self:
        _reject_raw_attachment_flag_state(self)
        return self

    @field_serializer(
        "production_route_attached",
        "production_transcript_attached",
        "production_sse_attached",
        "user_output_attached",
        "telegram_attached",
        "live_tool_attached",
        "live_runner_attached",
        "production_storage_attached",
        "production_queue_attached",
        "evidence_block_attached",
    )
    def _serialize_false_flags(self, _value: object) -> bool:
        return False


class Gate3BProductionAuthorityFlags(_Gate3BModel):
    can_delay_typescript_response: Literal[False] = Field(
        default=False,
        alias="canDelayTypescriptResponse",
    )
    can_alter_typescript_response: Literal[False] = Field(
        default=False,
        alias="canAlterTypescriptResponse",
    )
    can_block_typescript_response: Literal[False] = Field(
        default=False,
        alias="canBlockTypescriptResponse",
    )
    can_influence_user_output: Literal[False] = Field(
        default=False,
        alias="canInfluenceUserOutput",
    )
    python_response_authority: Literal[False] = Field(
        default=False,
        alias="pythonResponseAuthority",
    )
    typescript_response_authority_only: Literal[True] = Field(
        default=True,
        alias="typescriptResponseAuthorityOnly",
    )

    def __getattribute__(self, name: str) -> object:
        if name in _AUTHORITY_FALSE_KEYS:
            return False
        if name == "typescript_response_authority_only":
            return True
        return super().__getattribute__(name)

    @model_validator(mode="before")
    @classmethod
    def _reject_authority_input(cls, value: object) -> object:
        if isinstance(value, Gate3BProductionAuthorityFlags):
            _reject_raw_authority_flag_state(value)
            return value.model_dump(by_alias=True, mode="python", warnings=False)
        if isinstance(value, Mapping):
            for raw_key, flag_value in value.items():
                normalized = _normalize_gate3b_key(str(raw_key))
                if normalized == "typescript_response_authority_only":
                    if flag_value is not True:
                        raise ValueError("Gate 3B TypeScript authority-only flag must be true")
                    continue
                if flag_value is not False:
                    raise ValueError("Gate 3B production authority flags must be false")
        return value

    @model_validator(mode="after")
    def _reject_constructed_authority_state(self) -> Self:
        _reject_raw_authority_flag_state(self)
        return self

    @field_serializer(
        "can_delay_typescript_response",
        "can_alter_typescript_response",
        "can_block_typescript_response",
        "can_influence_user_output",
        "python_response_authority",
    )
    def _serialize_false_authority(self, _value: object) -> bool:
        return False

    @field_serializer("typescript_response_authority_only")
    def _serialize_typescript_only_authority(self, _value: object) -> bool:
        return True


class Gate3BSourceProvenance(_Gate3BModel):
    source_kind: Literal["live_duplicate_validation_metadata"] = Field(alias="sourceKind")
    capture_id: str = Field(alias="captureId")
    capture_surface: Literal["selected_bot_post_turn_bundle"] = Field(alias="captureSurface")
    capture_point: Literal["post_turn_redacted_duplicate"] = Field(alias="capturePoint")
    source_path: str = Field(alias="sourcePath")
    production_path_included: Literal[False] = Field(
        default=False,
        alias="productionPathIncluded",
    )
    live_traffic_consumed: Literal[False] = Field(default=False, alias="liveTrafficConsumed")

    @field_validator("capture_id", "source_path")
    @classmethod
    def _reject_unsafe_source_strings(cls, value: str) -> str:
        _reject_empty_or_unsafe_gate3b_string(value)
        return value


class Gate3BBundleRecipe(_Gate3BModel):
    recipe_snapshot_id: str = Field(alias="recipeSnapshotId")
    immutable_snapshot_id: str = Field(alias="immutableSnapshotId")
    pack_ids: tuple[str, ...] = Field(default=(), alias="packIds")
    hard_safety_enabled: Literal[True] = Field(alias="hardSafetyEnabled")

    @field_validator("recipe_snapshot_id", "immutable_snapshot_id")
    @classmethod
    def _reject_unsafe_recipe_ids(cls, value: str) -> str:
        _reject_empty_or_unsafe_gate3b_string(value)
        return value

    @field_validator("pack_ids")
    @classmethod
    def _reject_unsafe_pack_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _reject_empty_or_unsafe_gate3b_string(item)
        return value

    @model_validator(mode="after")
    def _require_immutable_snapshot_match(self) -> Self:
        if self.immutable_snapshot_id != self.recipe_snapshot_id:
            raise ValueError("Gate 3B immutable recipe snapshot ID must match recipeSnapshotId")
        return self


class Gate3BTurn(_Gate3BModel):
    session_ref: str = Field(default="redacted-session", alias="sessionRef")
    turn_id: str = Field(default="turn_redacted_0001", alias="turnId")
    agent_role: str = Field(default="assistant", alias="agentRole")
    spawn_depth: int = Field(default=0, alias="spawnDepth")
    channel: Literal["local_replay"] = "local_replay"

    @field_validator("session_ref", "turn_id", "agent_role")
    @classmethod
    def _reject_unsafe_turn_strings(cls, value: str) -> str:
        _reject_empty_or_unsafe_gate3b_string(value)
        return value

    @field_validator("spawn_depth")
    @classmethod
    def _reject_negative_spawn_depth(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Gate 3B spawnDepth must be non-negative")
        return value


class Gate3BJsonRecord(BaseModel):
    model_config = _JSON_RECORD_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _validate_record_payload(cls, value: object) -> object:
        if isinstance(value, Gate3BJsonRecord):
            return value.model_dump(by_alias=True, mode="python", warnings=False)
        if not isinstance(value, Mapping):
            raise ValueError("Gate 3B record entries must be JSON objects")
        _reject_unsafe_gate3b_value(value)
        return value

    @model_validator(mode="after")
    def _freeze_record_payload(self) -> Self:
        raw_extra = getattr(self, "__pydantic_extra__", None)
        if raw_extra is not None:
            object.__setattr__(self, "__pydantic_extra__", _freeze_gate3b_json_mapping(raw_extra))
        return self

    def as_dict(self) -> dict[str, object]:
        dumped = self.model_dump(mode="json", by_alias=True, warnings=False)
        _reject_unsafe_gate3b_value(dumped)
        return _thaw_gate3b_json_mapping(dumped)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        dumped = super().model_dump(*args, **kwargs)
        _reject_unsafe_gate3b_value(dumped)
        return dumped

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        dumped_json = super().model_dump_json(*args, **kwargs)
        _reject_unsafe_gate3b_value(json.loads(dumped_json))
        return dumped_json


class Gate3BRecordedToolResult(_Gate3BModel):
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    status: Literal["recorded"] = "recorded"
    output_metadata: dict[str, object] = Field(
        default_factory=dict,
        alias="outputMetadata",
    )
    dispatched_live: Literal[False] = Field(default=False, alias="dispatchedLive")

    @model_validator(mode="before")
    @classmethod
    def _validate_tool_result_payload(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_gate3b_value(value, _path=("recorded_tool_results",))
        return value

    @field_validator("tool_call_id", "tool_name", "status")
    @classmethod
    def _reject_unsafe_tool_strings(cls, value: str) -> str:
        _reject_empty_or_unsafe_gate3b_string(value)
        return value

    @field_validator("output_metadata")
    @classmethod
    def _validate_output_metadata(cls, value: dict[str, object]) -> _FrozenJsonDict:
        _reject_unsafe_gate3b_value(value, _path=("output_metadata",))
        return _freeze_gate3b_json_mapping(value)

    @field_serializer("output_metadata")
    def _serialize_output_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        _reject_unsafe_gate3b_value(value, _path=("output_metadata",))
        return _thaw_gate3b_json_mapping(value)

    @field_serializer("status")
    def _serialize_recorded_status(self, _value: object) -> Literal["recorded"]:
        return "recorded"

    @field_serializer("dispatched_live")
    def _serialize_not_dispatched_live(self, _value: object) -> bool:
        return False


class Gate3BLiveDuplicateBundle(_Gate3BModel):
    schema_version: Literal["gate3b.liveDuplicateBundle.v1"] = Field(alias="schemaVersion")
    bundle_id: str = Field(alias="bundleId")
    capture_surface: Literal["selected_bot_post_turn_bundle"] = Field(alias="captureSurface")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    response_authority: Literal["typescript-only"] = Field(alias="responseAuthority")
    recording_mode: Literal["live_duplicate_redacted"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    created_at: str = Field(alias="createdAt")
    source_provenance: Gate3BSourceProvenance = Field(alias="sourceProvenance")
    recipe: Gate3BBundleRecipe
    turn: Gate3BTurn = Field(default_factory=Gate3BTurn)
    transcript_entries: tuple[Gate3BJsonRecord, ...] = Field(
        default=(),
        alias="transcriptEntries",
    )
    agent_events: tuple[Gate3BJsonRecord, ...] = Field(default=(), alias="agentEvents")
    control_events: tuple[Gate3BJsonRecord, ...] = Field(default=(), alias="controlEvents")
    recorded_tool_results: tuple[Gate3BRecordedToolResult, ...] = Field(
        default=(),
        alias="recordedToolResults",
    )
    evidence_audit_metadata: Gate3BJsonRecord = Field(alias="evidenceAuditMetadata")
    attachment_flags: Gate3BAttachmentFlags = Field(
        default_factory=Gate3BAttachmentFlags,
        alias="attachmentFlags",
    )
    production_authority_flags: Gate3BProductionAuthorityFlags = Field(
        default_factory=Gate3BProductionAuthorityFlags,
        alias="productionAuthorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_payload(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_gate3b_value(value)
        return value

    @field_validator("bundle_id", "created_at")
    @classmethod
    def _reject_unsafe_bundle_strings(cls, value: str) -> str:
        _reject_empty_or_unsafe_gate3b_string(value)
        return value

    @model_validator(mode="after")
    def _require_live_duplicate_boundary(self) -> Self:
        _reject_raw_attachment_flag_state(self.attachment_flags)
        _reject_raw_authority_flag_state(self.production_authority_flags)
        if self.source_provenance.capture_surface != self.capture_surface:
            raise ValueError("Gate 3B source provenance capture surface must match bundle")
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(
        self,
        _value: Gate3BAttachmentFlags,
    ) -> dict[str, bool]:
        return Gate3BAttachmentFlags().model_dump(by_alias=True, mode="json", warnings=False)

    @field_serializer("production_authority_flags")
    def _serialize_authority_flags(
        self,
        _value: Gate3BProductionAuthorityFlags,
    ) -> dict[str, bool]:
        return Gate3BProductionAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, str | bool):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("Gate 3B payloads must contain only JSON-compatible values")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("Gate 3B payloads must contain only string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("Gate 3B payloads must contain only JSON-compatible values")


def _freeze_gate3b_json(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_gate3b_json_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_gate3b_json(item) for item in value)
    return value


def _freeze_gate3b_json_mapping(value: Mapping[str, object]) -> _FrozenJsonDict:
    return _FrozenJsonDict(
        {str(key): _freeze_gate3b_json(nested_value) for key, nested_value in value.items()}
    )


def _thaw_gate3b_json(value: object) -> object:
    if isinstance(value, Mapping):
        return _thaw_gate3b_json_mapping(value)
    if isinstance(value, list | tuple):
        return [_thaw_gate3b_json(item) for item in value]
    return value


def _thaw_gate3b_json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _thaw_gate3b_json(nested_value) for key, nested_value in value.items()}


def _reject_unsafe_gate3b_value(
    value: object,
    *,
    _path: tuple[str, ...] = (),
) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        _reject_unsafe_gate3b_string(value)
        return
    if isinstance(value, Mapping):
        for raw_key, nested_value in value.items():
            normalized_key = _normalize_gate3b_key(raw_key)
            if _is_allowed_false_gate3b_key(normalized_key, nested_value, parent_path=_path):
                pass
            elif _is_allowed_true_gate3b_key(normalized_key, nested_value, parent_path=_path):
                pass
            elif normalized_key in _FALSE_ONLY_METADATA_KEYS:
                raise ValueError("Gate 3B false-only metadata must be false")
            elif any(
                token in normalized_key.replace("_", "")
                for token in _FALSE_ONLY_COMPACT_KEY_TOKENS
            ):
                raise ValueError("Gate 3B false-only metadata aliases must be exact")
            elif _is_credential_key(normalized_key):
                raise ValueError("Gate 3B bundle must not contain credential keys")
            elif normalized_key in _PRIVATE_KEYS:
                raise ValueError(
                    "Gate 3B bundle must not contain hidden reasoning or private previews"
                )
            elif _is_forbidden_gate3b_key(normalized_key):
                raise ValueError(
                    "Gate 3B bundle must not contain production attachment, authority, "
                    "child, workspace, scheduler, signed ack, or evidence block keys"
                )
            elif _is_forbidden_execution_declared_surface(
                normalized_key,
                nested_value,
                parent_path=_path,
            ):
                raise ValueError(
                    "Gate 3B execution surfaces must remain recorded metadata only"
                )
            elif _is_malformed_execution_surface(normalized_key, nested_value):
                raise ValueError("Gate 3B executionSurface metadata must be an object")
            elif _is_execution_claim_key(normalized_key):
                if nested_value is not False:
                    raise ValueError("Gate 3B execution metadata must be recorded false only")
            next_path = (*_path, normalized_key)
            _reject_unsafe_gate3b_value(nested_value, _path=next_path)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_gate3b_value(item, _path=_path)


def _is_allowed_false_gate3b_key(
    normalized_key: str,
    value: object,
    *,
    parent_path: tuple[str, ...],
) -> bool:
    if value is not False:
        return False
    if normalized_key in _FALSE_ONLY_METADATA_KEYS:
        return True
    if normalized_key == "external_ack_included":
        return True
    parent = parent_path[-1] if parent_path else ""
    if parent == "attachment_flags" and normalized_key in _ATTACHMENT_FALSE_KEYS:
        return True
    if parent == "production_authority_flags" and normalized_key in _AUTHORITY_FALSE_KEYS:
        return True
    if parent in {"execution_surface", "output_metadata"} and _is_execution_claim_key(normalized_key):
        return True
    allowed_keys = _SAFE_FALSE_KEYS_BY_PARENT.get(parent)
    return bool(allowed_keys and normalized_key in allowed_keys)


def _is_allowed_true_gate3b_key(
    normalized_key: str,
    value: object,
    *,
    parent_path: tuple[str, ...],
) -> bool:
    parent = parent_path[-1] if parent_path else ""
    return bool(
        value is True
        and (
            (parent == "production_authority_flags" and normalized_key == "typescript_response_authority_only")
            or (parent == "recipe" and normalized_key == "hard_safety_enabled")
            or normalized_key == "recorded_only"
        )
    )


def _is_forbidden_execution_declared_surface(
    normalized_key: str,
    value: object,
    *,
    parent_path: tuple[str, ...],
) -> bool:
    if normalized_key != "declared_surface":
        return False
    if not isinstance(value, str):
        return True
    return _normalize_gate3b_key(value) not in _ALLOWED_EXECUTION_DECLARED_SURFACES


def _is_malformed_execution_surface(normalized_key: str, value: object) -> bool:
    return normalized_key == "execution_surface" and not isinstance(value, Mapping)


def _is_credential_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if compact_key in {"apikey", "telegramtoken", "rawauthheaders", "connectorcredentials"}:
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    credential_pairs = (
        {"api", "key"},
        {"access", "key"},
        {"client", "secret"},
        {"provider", "key"},
        {"service", "key"},
    )
    if any(pair <= parts for pair in credential_pairs):
        return True
    return bool(parts & _CREDENTIAL_KEY_PARTS)


def _is_execution_claim_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if normalized_key in _EXECUTION_CLAIM_KEYS:
        return True
    return bool(
        (
            "executed" in compact_key
            or "sideeffects" in compact_key
            or "invoked" in compact_key
            or "dispatched" in compact_key
        )
        and any(
            token in compact_key
            for token in ("shell", "code", "tool", "package", "script", "command", "external")
        )
    )


def _is_forbidden_gate3b_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if compact_key in _FORBIDDEN_COMPACT_KEY_TOKENS:
        return True
    if any(token in compact_key for token in _FORBIDDEN_COMPACT_KEY_TOKENS):
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    if {"signed", "ack"} <= parts or {"external", "ack"} <= parts:
        return True
    if {"evidence", "block"} <= parts:
        return True
    if {"custom", "extractor"} <= parts:
        return True
    if (
        {"workspace", "mutation"} <= parts
        or {"workspace", "adoption"} <= parts
        or {"workspace", "attached"} <= parts
        or {"workspace", "mounted"} <= parts
    ):
        return True
    if {"live", "capture", "consumed"} <= parts:
        return True
    if {"raw", "connector", "credentials", "included"} <= parts:
        return True
    if {"pvc", "mounted"} <= parts:
        return True
    if {"adk", "runner"} <= parts and parts & {"invoked", "attached"}:
        return True
    if {"child", "execution"} <= parts:
        return True
    if "production" in parts and parts & {"attached", "write", "append", "route", "sse"}:
        return True
    if "authority" in parts and parts & {"python", "delay", "alter", "block", "influence"}:
        return True
    return False


def _reject_empty_or_unsafe_gate3b_string(value: str) -> None:
    if not value.strip():
        raise ValueError("Gate 3B bundle fields must be non-empty")
    _reject_unsafe_gate3b_string(value)


def _reject_unsafe_gate3b_string(value: str) -> None:
    normalized = value.strip()
    if not normalized:
        return
    if normalized in _ALLOWED_GATE3B_LITERALS:
        return
    path_parts = tuple(part for part in re.split(r"[\\/]+", normalized) if part)
    if _ABSOLUTE_PATH_RE.search(normalized):
        raise ValueError("Gate 3B payloads must not contain absolute paths")
    if ".." in path_parts or "/.." in normalized or "\\.." in normalized:
        raise ValueError("Gate 3B payloads must not contain parent path traversal")
    if _UNSAFE_TEXT_RE.search(normalized):
        raise ValueError("Gate 3B payloads must not contain production paths or hosts")
    if _SECRET_TEXT_RE.search(normalized):
        raise ValueError("Gate 3B payloads must not contain credential-shaped strings")
    if _EXECUTION_TEXT_RE.search(normalized):
        raise ValueError("Gate 3B payloads must not claim live execution or side effects")
    lowered = normalized.lower()
    compact_value = _compact_gate3b_text(normalized)
    if any(token in compact_value for token in _FORBIDDEN_COMPACT_VALUE_TOKENS):
        raise ValueError(
            "Gate 3B payloads must not claim private reasoning, production attachment, "
            "live runner, scheduler, workspace, signed acknowledgement, evidence block, "
            "custom extractor, or canary traffic surfaces"
        )
    if "chain of thought" in lowered or "hidden reasoning" in lowered:
        raise ValueError("Gate 3B payloads must not contain hidden reasoning")


def _reject_raw_attachment_flag_state(value: Gate3BAttachmentFlags) -> None:
    raw_state = object.__getattribute__(value, "__dict__")
    if not isinstance(raw_state, Mapping):
        raise ValueError("Gate 3B attachment flags raw state must be a mapping")
    for field_name in Gate3BAttachmentFlags.model_fields:
        if raw_state.get(field_name) is not False:
            raise ValueError("Gate 3B attachment flags must remain false")
        if getattr(value, field_name) is not False:
            raise ValueError("Gate 3B attachment flags must remain false")


def _reject_raw_authority_flag_state(value: Gate3BProductionAuthorityFlags) -> None:
    raw_state = object.__getattribute__(value, "__dict__")
    if not isinstance(raw_state, Mapping):
        raise ValueError("Gate 3B production authority flags raw state must be a mapping")
    for field_name in Gate3BProductionAuthorityFlags.model_fields:
        expected = field_name == "typescript_response_authority_only"
        if raw_state.get(field_name) is not expected:
            raise ValueError("Gate 3B production authority flags are immutable")
        if getattr(value, field_name) is not expected:
            raise ValueError("Gate 3B production authority flags are immutable")


def _normalize_gate3b_key(value: str) -> str:
    acronym_spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value)
    camel_spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", acronym_spaced)
    return re.sub(r"[^a-z0-9]+", "_", camel_spaced.lower()).strip("_")


def _compact_gate3b_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_gate3b_key(value))


def _gate3b_model_dump_validation_path(model: BaseModel) -> tuple[str, ...]:
    root_by_model_name = {
        "Gate3BAttachmentFlags": "attachment_flags",
        "Gate3BProductionAuthorityFlags": "production_authority_flags",
        "Gate3BSourceProvenance": "source_provenance",
        "Gate3BBundleRecipe": "recipe",
        "Gate3BRecordedToolResult": "recorded_tool_results",
    }
    root = root_by_model_name.get(model.__class__.__name__)
    if root is None:
        return ()
    return (root,)


def _resolve_gate3b_bundle_path(path: str | Path, *, bundle_root: str | Path | None) -> Path:
    _reject_unsafe_gate3b_bundle_path_text(str(path))
    candidate = Path(path)
    if bundle_root is None:
        _reject_unsafe_gate3b_bundle_path_text(str(candidate.resolve(strict=False)))
        return candidate
    _reject_unsafe_gate3b_bundle_path_text(str(bundle_root))
    resolved_root = Path(bundle_root).resolve(strict=True)
    _reject_unsafe_gate3b_bundle_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_gate3b_bundle_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("Gate 3B bundle path must stay under bundle_root")
    return resolved_candidate


def _reject_unsafe_gate3b_bundle_path_text(path_text: str) -> None:
    if _GATE3B_BUNDLE_PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("Gate 3B bundle paths must be local-only and non-production")


def load_gate3b_live_duplicate_bundle(
    path: str | Path,
    *,
    bundle_root: str | Path | None = None,
) -> Gate3BLiveDuplicateBundle:
    bundle_path = _resolve_gate3b_bundle_path(path, bundle_root=bundle_root)
    with bundle_path.open("r", encoding="utf-8") as bundle_file:
        payload: Any = json.load(bundle_file)
    return Gate3BLiveDuplicateBundle.model_validate(payload)


__all__ = [
    "Gate3BAttachmentFlags",
    "Gate3BBundleRecipe",
    "Gate3BJsonRecord",
    "Gate3BLiveDuplicateBundle",
    "Gate3BProductionAuthorityFlags",
    "Gate3BRecordedToolResult",
    "Gate3BSourceProvenance",
    "Gate3BTurn",
    "load_gate3b_live_duplicate_bundle",
]
