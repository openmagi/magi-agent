from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import json
import re
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


OpenCodeDeltaStatus = Literal["covered", "missing", "delegated"]
OPENCODE_LATEST_COMMIT = "34b1-045a-016e-22aa-fbaf-0719-fd7c-8553-defe-bdc4".replace("-", "")

REQUIRED_OPENCODE_DELTA_ROWS = (
    "provider_compatibility_fixtures",
    "snapshot_pre_stream_digest_boundary",
    "output_budgeting_artifact_contract",
    "auto_permission_receipts_no_behavior_drift",
    "shell_metadata_contract",
    "repetition_guard_contract",
    "runtime_plan_mode_capability",
    "edit_patch_fidelity_contract",
    "lsp_lifecycle_contract",
    "runtime_event_replay_fence",
    "todo_projection_contract",
    "client_protocol_boundary",
    "mcp_lifecycle_status_config",
    "provider_header_provenance_allowlist",
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_:-]{2,180}$")
_UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"ask[- ]?to[- ]?allow|"
    r"raw\s+/tmp|/tmp/|"
    r"broad\s+raw\s+workspace\s+snapshot|raw\s+workspace\s+snapshot|"
    r"model[- ]?provided\s+authority|"
    r"denylist[- ]?only|"
    r"process[- ]?memory\s+durable\s+jobs?|"
    r"opencode\s+runtime\s+kernel|"
    r"live\s+authority\s+allowed|"
    r"authorization\s*:\s*bearer|"
    r"\bcookie\s*:|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"\bsk-[A-Za-z0-9._-]{8,}|"
    r"/(?:data/bots|workspace|var/lib/kubelet)(?:/[^\s\"',}]+)*"
    r")",
    re.IGNORECASE,
)


class OpenCodeDeltaAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    live_authority_allowed: Literal[False] = Field(default=False, alias="liveAuthorityAllowed")
    default_on_allowed: Literal[False] = Field(default=False, alias="defaultOnAllowed")
    core_touch_allowed: Literal[False] = Field(default=False, alias="coreTouchAllowed")
    model_metadata_authority_allowed: Literal[False] = Field(
        default=False,
        alias="modelMetadataAuthorityAllowed",
    )
    toolhost_bypass_allowed: Literal[False] = Field(default=False, alias="toolHostBypassAllowed")
    raw_workspace_snapshot_allowed: Literal[False] = Field(
        default=False,
        alias="rawWorkspaceSnapshotAllowed",
    )
    live_provider_header_activation_allowed: Literal[False] = Field(
        default=False,
        alias="liveProviderHeaderActivationAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    @field_serializer(
        "live_authority_allowed",
        "default_on_allowed",
        "core_touch_allowed",
        "model_metadata_authority_allowed",
        "toolhost_bypass_allowed",
        "raw_workspace_snapshot_allowed",
        "live_provider_header_activation_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class OpenCodeDeltaRow(BaseModel):
    model_config = _MODEL_CONFIG

    row_id: str = Field(alias="rowId")
    opencode_commit: str = Field(alias="opencodeCommit")
    opencode_source: str = Field(alias="opencodeSource")
    planned_slice: str = Field(alias="plannedSlice")
    openmagi_target: tuple[str, ...] = Field(alias="openmagiTarget")
    owning_layer: str = Field(alias="owningLayer")
    core_touch_allowed: Literal[False] = Field(alias="coreTouchAllowed")
    core_touch_reason: str | None = Field(default=None, alias="coreTouchReason")
    status: OpenCodeDeltaStatus
    activation_gate: str = Field(alias="activationGate")
    default_off: Literal[True] = Field(alias="defaultOff")
    live_authority_allowed: Literal[False] = Field(alias="liveAuthorityAllowed")
    notes: str

    @field_validator(
        "row_id",
        "opencode_commit",
        "opencode_source",
        "planned_slice",
        "owning_layer",
        "activation_gate",
        "notes",
    )
    @classmethod
    def _validate_text_field(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("OpenCode delta matrix text fields must be non-empty")
        _reject_unsafe_text(value)
        return value

    @field_validator("opencode_commit")
    @classmethod
    def _validate_opencode_commit(cls, value: str) -> str:
        if value != OPENCODE_LATEST_COMMIT:
            raise ValueError("opencodeCommit must be pinned to the reviewed latest OpenCode commit")
        return value

    @field_validator("opencode_source")
    @classmethod
    def _validate_opencode_source(cls, value: str) -> str:
        prefix = f"anomalyco/opencode@{OPENCODE_LATEST_COMMIT}:"
        if not value.startswith(prefix):
            raise ValueError("opencodeSource must include the pinned repository commit and source path")
        source_path = value.removeprefix(prefix)
        if not source_path.startswith("packages/opencode/src/"):
            raise ValueError("opencodeSource must point at a package source path")
        return value

    @field_validator("row_id")
    @classmethod
    def _validate_row_id(cls, value: str) -> str:
        if not _SAFE_ID_RE.fullmatch(value):
            raise ValueError("rowId must be a safe machine-readable identifier")
        return value

    @field_validator("openmagi_target")
    @classmethod
    def _validate_openmagi_target(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("openmagiTarget must cite at least one file, test, or plan")
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("openmagiTarget entries must be non-empty strings")
            _reject_unsafe_text(item)
        return tuple(dict.fromkeys(item.strip() for item in value))

    @model_validator(mode="after")
    def _validate_authority_invariants(self) -> Self:
        if self.core_touch_reason not in (None, ""):
            raise ValueError("coreTouchReason must be empty when coreTouchAllowed=false")
        if self.status not in {"covered", "missing", "delegated"}:
            raise ValueError("status must decide covered, missing, or delegated")
        return self


class OpenCodeDeltaMatrix(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["opencodeHarnessDeltaMatrix.v1"] = Field(alias="schemaVersion")
    matrix_id: str = Field(alias="matrixId")
    generated_for: str = Field(alias="generatedFor")
    source_policy: str = Field(alias="sourcePolicy")
    authority_flags: OpenCodeDeltaAuthorityFlags = Field(alias="authorityFlags")
    rows: tuple[OpenCodeDeltaRow, ...]

    @field_validator("matrix_id", "generated_for", "source_policy")
    @classmethod
    def _validate_matrix_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("matrix metadata text fields must be non-empty")
        _reject_unsafe_text(value)
        return value

    @model_validator(mode="after")
    def _validate_rows(self) -> Self:
        row_ids = tuple(row.row_id for row in self.rows)
        if row_ids != REQUIRED_OPENCODE_DELTA_ROWS:
            raise ValueError("OpenCode delta rows must match the required PR-0 order")
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("OpenCode delta rowIds must be unique")
        if any(row.live_authority_allowed for row in self.rows):
            raise ValueError("liveAuthorityAllowed must be false for every row")
        if any(row.default_off is not True for row in self.rows):
            raise ValueError("defaultOff must be true for every row")
        return self


class OpenCodeDeltaProjection(BaseModel):
    model_config = _MODEL_CONFIG

    matrix_id: str = Field(alias="matrixId")
    row_order: tuple[str, ...] = Field(alias="rowOrder")
    by_status: dict[str, int] = Field(alias="byStatus")
    covered_rows: tuple[str, ...] = Field(alias="coveredRows")
    delegated_rows: tuple[str, ...] = Field(alias="delegatedRows")
    missing_rows: tuple[str, ...] = Field(alias="missingRows")
    no_live_authority: bool = Field(alias="noLiveAuthority")
    default_off: bool = Field(alias="defaultOff")
    core_touch_allowed_count: int = Field(alias="coreTouchAllowedCount")
    authority_flags: OpenCodeDeltaAuthorityFlags = Field(alias="authorityFlags")


def load_opencode_delta_matrix(
    path: str | Path = "harness_delta_matrix.json",
    *,
    fixture_root: str | Path | None = None,
) -> OpenCodeDeltaMatrix:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return OpenCodeDeltaMatrix.model_validate(payload)


def project_opencode_delta_matrix(
    matrix: OpenCodeDeltaMatrix | Mapping[str, object],
) -> OpenCodeDeltaProjection:
    safe_matrix = _validated_matrix_snapshot(matrix)
    by_status = Counter(row.status for row in safe_matrix.rows)
    return OpenCodeDeltaProjection(
        matrixId=safe_matrix.matrix_id,
        rowOrder=tuple(row.row_id for row in safe_matrix.rows),
        byStatus={status: by_status[status] for status in ("covered", "delegated", "missing")},
        coveredRows=tuple(row.row_id for row in safe_matrix.rows if row.status == "covered"),
        delegatedRows=tuple(row.row_id for row in safe_matrix.rows if row.status == "delegated"),
        missingRows=tuple(row.row_id for row in safe_matrix.rows if row.status == "missing"),
        noLiveAuthority=all(row.live_authority_allowed is False for row in safe_matrix.rows),
        defaultOff=all(row.default_off is True for row in safe_matrix.rows),
        coreTouchAllowedCount=sum(1 for row in safe_matrix.rows if row.core_touch_allowed),
        authorityFlags=safe_matrix.authority_flags,
    )


def _validated_matrix_snapshot(
    matrix: OpenCodeDeltaMatrix | Mapping[str, object],
) -> OpenCodeDeltaMatrix:
    if isinstance(matrix, OpenCodeDeltaMatrix):
        return OpenCodeDeltaMatrix.model_validate(
            matrix.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return OpenCodeDeltaMatrix.model_validate(matrix)


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("absolute fixture paths are not allowed")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("fixture paths must stay under the fixture root")
    root = Path.cwd() if fixture_root is None else Path(fixture_root)
    resolved = root / candidate
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _reject_unsafe_text(value: object) -> None:
    if isinstance(value, str):
        if _UNSAFE_TEXT_RE.search(value):
            raise ValueError("OpenCode delta contract text contains unsafe guidance")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_unsafe_text(str(key))
            _reject_unsafe_text(nested)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_text(item)


__all__ = [
    "OpenCodeDeltaAuthorityFlags",
    "OpenCodeDeltaMatrix",
    "OpenCodeDeltaProjection",
    "OpenCodeDeltaRow",
    "REQUIRED_OPENCODE_DELTA_ROWS",
    "load_opencode_delta_matrix",
    "project_opencode_delta_matrix",
]
