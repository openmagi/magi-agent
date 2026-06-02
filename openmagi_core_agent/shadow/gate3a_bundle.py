from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from openmagi_core_agent.shadow.fixture_runner import (
    _is_credential_comparison_metadata_key,
    _normalize_live_surface_string,
    _reject_production_like_string,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_PROVENANCE_KEYS = frozenset(
    {
        "source_kind",
        "source_path",
        "production_path_included",
        "live_capture_included",
    }
)
_FORBIDDEN_PRIVATE_KEYS = frozenset(
    {
        "hidden_reasoning",
        "chain_of_thought",
        "private_reasoning",
        "reasoning_trace",
        "private_tool_preview",
        "private_tool_input",
        "private_tool_output",
        "raw_tool_preview",
    }
)
_SAFE_TYPED_FALSE_KEYS = frozenset(
    {
        "production_path_included",
        "live_capture_included",
        "dispatched_live",
    }
)
_FORBIDDEN_GATE3A_KEY_PARTS = frozenset(
    {
        "child",
        "workspace",
        "scheduler",
    }
)
_FORBIDDEN_GATE3A_COMPACT_KEY_TOKENS = frozenset(
    {
        "livesurface",
        "liveexecution",
        "livetool",
        "livecapture",
        "productionroute",
        "productionstorage",
        "productionpath",
        "productionattached",
        "trafficattached",
        "routeattached",
        "outputattachment",
        "outputattached",
        "uservisibleoutput",
        "toolsideeffects",
        "childexecution",
        "workspacemutation",
        "workspaceadoption",
        "schedulerrun",
        "schedulerresume",
        "customextractor",
        "signedack",
        "signedexternalack",
        "evidenceblock",
        "evidenceblockmode",
    }
)
_FORBIDDEN_TRUE_EXECUTION_KEYS = frozenset(
    {
        "auto_executed",
        "generated_script_executed",
    }
)
_GATE3A_BUNDLE_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|"
    r"(?:^|[\\/])missions?(?:[\\/]|$)|(?:^|[\\/])schedulers?(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:mission|scheduler)-store(?:[\\/]|$)|"
    r"bot-[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


class Gate3ABundleSourceProvenance(BaseModel):
    model_config = _MODEL_CONFIG

    source_kind: Literal["local_fixture", "isolated_local"] = Field(alias="sourceKind")
    source_path: str = Field(alias="sourcePath")
    production_path_included: Literal[False] = Field(
        default=False,
        alias="productionPathIncluded",
    )
    live_capture_included: Literal[False] = Field(
        default=False,
        alias="liveCaptureIncluded",
    )

    @field_validator("source_path")
    @classmethod
    def _reject_production_source_path(cls, value: str) -> str:
        _reject_production_like_string(value)
        return value


class Gate3ABundleTurn(BaseModel):
    model_config = _MODEL_CONFIG

    session_ref: str = Field(alias="sessionRef")
    turn_id: str = Field(alias="turnId")
    agent_role: str = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    channel: Literal["local_replay", "isolated_replay"]

    @field_validator("session_ref", "turn_id", "agent_role", "channel")
    @classmethod
    def _reject_unsafe_turn_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gate 3A turn fields must be non-empty")
        _reject_production_like_string(value)
        return value

    @field_validator("spawn_depth")
    @classmethod
    def _reject_negative_spawn_depth(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Gate 3A spawnDepth must be non-negative")
        return value


class Gate3ABundleRecipe(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_snapshot_id: str = Field(alias="recipeSnapshotId")
    pack_ids: tuple[str, ...] = Field(default=(), alias="packIds")
    hard_safety_enabled: Literal[True] = Field(alias="hardSafetyEnabled")

    @field_validator("recipe_snapshot_id")
    @classmethod
    def _reject_unsafe_recipe_snapshot_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gate 3A recipeSnapshotId must be non-empty")
        _reject_production_like_string(value)
        return value

    @field_validator("pack_ids")
    @classmethod
    def _reject_unsafe_pack_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Gate 3A packIds must be non-empty")
        for item in value:
            _reject_production_like_string(item)
        return value


class Gate3AJsonRecord(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="allow",
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_record_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("Gate 3A record entries must be JSON objects")
        _reject_unsafe_bundle_value(value)
        return value

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, warnings=False)


class Gate3ARecordedToolResult(BaseModel):
    model_config = _MODEL_CONFIG

    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    status: Literal["recorded"] = "recorded"
    output_metadata: dict[str, object] = Field(
        default_factory=dict,
        alias="outputMetadata",
    )
    dispatched_live: Literal[False] = Field(default=False, alias="dispatchedLive")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied)

    @model_validator(mode="before")
    @classmethod
    def _validate_tool_result_payload(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_bundle_value(value, _path=("recorded_tool_results",))
        return value

    @field_serializer("status")
    def _serialize_status(self, _value: object) -> Literal["recorded"]:
        return "recorded"

    @field_validator("tool_call_id", "tool_name", "status")
    @classmethod
    def _reject_unsafe_tool_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gate 3A tool result fields must be non-empty")
        _reject_production_like_string(value)
        return value

    @field_validator("output_metadata")
    @classmethod
    def _validate_output_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        _reject_unsafe_bundle_value(value, _path=("output_metadata",))
        return value


class Gate3ARecordedBundle(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate3a.recordedBundle.v1"] = Field(alias="schemaVersion")
    bundle_id: str = Field(alias="bundleId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["recorded_redacted"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    created_at: str = Field(alias="createdAt")
    source_provenance: Gate3ABundleSourceProvenance = Field(alias="sourceProvenance")
    turn: Gate3ABundleTurn
    recipe: Gate3ABundleRecipe
    transcript_entries: tuple[Gate3AJsonRecord, ...] = Field(
        default=(),
        alias="transcriptEntries",
    )
    agent_events: tuple[Gate3AJsonRecord, ...] = Field(default=(), alias="agentEvents")
    recorded_tool_results: tuple[Gate3ARecordedToolResult, ...] = Field(
        default=(),
        alias="recordedToolResults",
    )
    control_events: tuple[Gate3AJsonRecord, ...] = Field(
        default=(),
        alias="controlEvents",
    )
    evidence_records: tuple[Gate3AJsonRecord, ...] = Field(
        default=(),
        alias="evidenceRecords",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_payload(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_bundle_value(value)
        return value

    @field_validator("bundle_id", "created_at")
    @classmethod
    def _reject_unsafe_bundle_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gate 3A bundle fields must be non-empty")
        _reject_production_like_string(value)
        return value

    @model_validator(mode="after")
    def _require_local_recorded_boundary(self) -> Self:
        raw_extra = getattr(self, "__pydantic_extra__", None)
        if raw_extra:
            raise ValueError("Gate 3A bundle must not contain raw extra state")
        return self


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("Gate 3A payloads must contain only JSON-compatible values")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("Gate 3A payloads must contain only string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("Gate 3A payloads must contain only JSON-compatible values")


def _reject_unsafe_bundle_value(
    value: object,
    *,
    _path: tuple[str, ...] = (),
) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        _reject_production_like_string(value)
        return
    if isinstance(value, Mapping):
        for raw_key, nested_value in value.items():
            if isinstance(raw_key, str):
                normalized_key = _normalize_live_surface_string(raw_key)
                if _is_allowed_typed_false_bundle_key(
                    normalized_key,
                    nested_value,
                    parent_path=_path,
                ):
                    pass
                elif normalized_key in _FORBIDDEN_TRUE_EXECUTION_KEYS:
                    if not _is_allowed_false_recorded_execution_metadata(
                        nested_value,
                        parent_path=_path,
                    ):
                        raise ValueError(
                            "Gate 3A bundle execution evidence must be recorded false metadata only"
                        )
                elif normalized_key in _SAFE_TYPED_FALSE_KEYS:
                    raise ValueError(
                        "Gate 3A typed attachment flags are allowed only in their schema fields"
                    )
                elif (
                    normalized_key not in _SAFE_PROVENANCE_KEYS
                    and _is_credential_comparison_metadata_key(normalized_key)
                ):
                    raise ValueError("Gate 3A bundle must not contain credential keys")
                elif normalized_key in _FORBIDDEN_PRIVATE_KEYS:
                    raise ValueError(
                        "Gate 3A bundle must not contain hidden reasoning or private tool previews"
                    )
                elif _is_forbidden_gate3a_bundle_key(normalized_key):
                    raise ValueError(
                        "Gate 3A bundle must not contain live execution, output attachment, "
                        "child, workspace, scheduler, custom extractor, signed ack, or evidence block keys"
                    )
            next_path = (
                (*_path, _normalize_live_surface_string(raw_key))
                if isinstance(raw_key, str)
                else _path
            )
            _reject_unsafe_bundle_value(nested_value, _path=next_path)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_bundle_value(item, _path=_path)


def _is_allowed_typed_false_bundle_key(
    normalized_key: str,
    value: object,
    *,
    parent_path: tuple[str, ...],
) -> bool:
    if value is not False:
        return False
    parent = parent_path[-1] if parent_path else ""
    if normalized_key in {"production_path_included", "live_capture_included"}:
        return parent == "source_provenance"
    if normalized_key == "dispatched_live":
        return parent == "recorded_tool_results"
    return False


def _is_allowed_false_recorded_execution_metadata(
    value: object,
    *,
    parent_path: tuple[str, ...],
) -> bool:
    return value is False and "output_metadata" in parent_path


def _is_forbidden_gate3a_bundle_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if any(token in compact_key for token in _FORBIDDEN_GATE3A_COMPACT_KEY_TOKENS):
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    if parts & _FORBIDDEN_GATE3A_KEY_PARTS:
        return True
    if "custom" in parts and "extractor" in parts:
        return True
    if "signed" in parts and "ack" in parts:
        return True
    if "evidence" in parts and "block" in parts:
        return True
    return bool(
        "live" in parts
        and parts
        & {
            "attached",
            "attachment",
            "capture",
            "execution",
            "route",
            "tool",
            "traffic",
        }
    )


def _validated_gate3a_recorded_bundle_snapshot(
    bundle: Gate3ARecordedBundle,
) -> Gate3ARecordedBundle:
    raw_extra = getattr(bundle, "__pydantic_extra__", None)
    if raw_extra:
        raise ValueError("Gate 3A bundle must not contain raw extra state")
    payload = bundle.model_dump(by_alias=True, mode="json", warnings=False)
    return Gate3ARecordedBundle.model_validate(payload)


def _resolve_gate3a_bundle_path(path: str | Path, *, bundle_root: str | Path | None) -> Path:
    _reject_unsafe_gate3a_bundle_path_text(str(path))
    candidate = Path(path)
    if bundle_root is None:
        _reject_unsafe_gate3a_bundle_path_text(str(candidate.resolve(strict=False)))
        return candidate
    _reject_unsafe_gate3a_bundle_path_text(str(bundle_root))
    resolved_root = Path(bundle_root).resolve(strict=True)
    _reject_unsafe_gate3a_bundle_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_gate3a_bundle_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("Gate 3A bundle path must stay under bundle_root")
    return resolved_candidate


def _reject_unsafe_gate3a_bundle_path_text(path_text: str) -> None:
    if _GATE3A_BUNDLE_PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("Gate 3A bundle paths must be local-only and non-production")


def load_gate3a_recorded_bundle(
    path: str | Path,
    *,
    bundle_root: str | Path | None = None,
) -> Gate3ARecordedBundle:
    bundle_path = _resolve_gate3a_bundle_path(path, bundle_root=bundle_root)
    with bundle_path.open("r", encoding="utf-8") as bundle_file:
        payload: Any = json.load(bundle_file)
    return Gate3ARecordedBundle.model_validate(payload)


__all__ = [
    "Gate3ABundleRecipe",
    "Gate3ABundleSourceProvenance",
    "Gate3ABundleTurn",
    "Gate3AJsonRecord",
    "Gate3ARecordedBundle",
    "Gate3ARecordedToolResult",
    "load_gate3a_recorded_bundle",
]
