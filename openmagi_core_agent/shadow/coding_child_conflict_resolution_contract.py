from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


CodingChildConflictResolutionCategory = Literal[
    "unresolved_conflict_blocks",
    "later_resolver_clears",
    "same_spawn_reject_clears",
    "different_spawn_disposition_ignored",
    "same_spawn_apply_requires_reviewer",
    "same_spawn_cherry_pick_requires_reviewer",
]
CodingChildConflictResolutionState = Literal[
    "blocking_conflict",
    "conflict_metadata_cleared",
]
CodingChildConflictResolutionAction = Literal["reject", "apply", "cherry_pick"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_PATH_RE = re.compile(
    r"(?:^|[\\/])"
    r"(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet|Users|home|private|mnt)"
    r"(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_conflictsecret",
    "sk-conflict-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "raw diff",
    "diff --git",
    "--- a/",
    "+++ b/",
    "@@ -",
    "raw transcript",
    "rawTranscripts",
    "pythonResponseAuthority",
)
_FORBIDDEN_PUBLIC_TOKENS_NORMALIZED = tuple(
    token.casefold() for token in _FORBIDDEN_PUBLIC_TOKENS
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "canary_traffic_attached",
        "child_execution_attached",
        "code_executed",
        "evidence_block_enabled",
        "file_mutated",
        "git_executed",
        "live_adoption_attached",
        "live_tool_dispatched",
        "memory_provider_called",
        "patch_applied",
        "production_authority",
        "production_storage_written",
        "route_or_api_attached",
        "shell_or_code_executed",
        "storage_attached",
        "telegram_attached",
        "test_executed",
        "tool_host_dispatched",
        "workspace_mutated",
    }
)
_REQUIRED_CATEGORIES = set(
    CodingChildConflictResolutionCategory.__args__  # type: ignore[attr-defined]
)


class CodingChildConflictResolutionAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    tool_host_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    git_executed: Literal[False] = Field(default=False, alias="gitExecuted")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    test_executed: Literal[False] = Field(default=False, alias="testExecuted")
    file_mutated: Literal[False] = Field(default=False, alias="fileMutated")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    live_adoption_attached: Literal[False] = Field(
        default=False,
        alias="liveAdoptionAttached",
    )
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    storage_attached: Literal[False] = Field(default=False, alias="storageAttached")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "child_execution_attached",
        "tool_host_dispatched",
        "live_tool_dispatched",
        "git_executed",
        "shell_or_code_executed",
        "test_executed",
        "file_mutated",
        "workspace_mutated",
        "live_adoption_attached",
        "route_or_api_attached",
        "storage_attached",
        "production_storage_written",
        "production_authority",
        "canary_traffic_attached",
        "telegram_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class CodingChildConflictMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    spawn_ref: str = Field(alias="spawnRef")
    worktree_ref: str = Field(alias="worktreeRef")
    spawn_observed_at: int | float = Field(alias="spawnObservedAt")
    conflict_index: int | float = Field(alias="conflictIndex")
    latest_child_mutation_at: int | float = Field(alias="latestChildMutationAt")
    conflicted_files: tuple[str, ...] = Field(alias="conflictedFiles")
    changed_files: tuple[str, ...] = Field(alias="changedFiles")
    blocking: Literal[True]

    @model_validator(mode="after")
    def _validate_conflict(self) -> Self:
        _validate_ref_prefix(self.spawn_ref, "spawn:")
        _validate_ref_prefix(self.worktree_ref, "spawn-worktree:")
        _validate_number(self.spawn_observed_at, "spawnObservedAt")
        _validate_number(self.conflict_index, "conflictIndex")
        _validate_number(self.latest_child_mutation_at, "latestChildMutationAt")
        if self.latest_child_mutation_at < self.spawn_observed_at:
            raise ValueError("latest child mutation cannot predate conflict observation")
        if not self.conflicted_files:
            raise ValueError("conflict metadata requires conflicted files")
        for rel_path in (*self.conflicted_files, *self.changed_files):
            _reject_unsafe_relative_path(rel_path)
        if not set(self.conflicted_files).issubset(set(self.changed_files)):
            raise ValueError("conflicted files must be included in changed files")
        return self


class CodingChildConflictResolutionAttempt(BaseModel):
    model_config = _MODEL_CONFIG

    persona: Literal["conflict_resolver"]
    spawn_ref: str = Field(alias="spawnRef")
    spawn_started_at: int | float = Field(alias="spawnStartedAt")
    covered_files: tuple[str, ...] = Field(alias="coveredFiles")
    source: Literal["recorded_adk_event_metadata"] = "recorded_adk_event_metadata"

    @model_validator(mode="after")
    def _validate_attempt(self) -> Self:
        _validate_ref_prefix(self.spawn_ref, "spawn:")
        _validate_number(self.spawn_started_at, "spawnStartedAt")
        for rel_path in self.covered_files:
            _reject_unsafe_relative_path(rel_path)
        return self


class CodingChildConflictDisposition(BaseModel):
    model_config = _MODEL_CONFIG

    spawn_ref: str = Field(alias="spawnRef")
    action: CodingChildConflictResolutionAction
    observed_at: int | float = Field(alias="observedAt")
    result_index: int | float = Field(alias="resultIndex")
    source: Literal["recorded_spawn_worktree_disposition"] = (
        "recorded_spawn_worktree_disposition"
    )

    @model_validator(mode="after")
    def _validate_disposition(self) -> Self:
        _validate_ref_prefix(self.spawn_ref, "spawn:")
        _validate_number(self.observed_at, "observedAt")
        _validate_number(self.result_index, "resultIndex")
        return self


class CodingChildReviewerFreshnessMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    required: bool
    satisfied: bool
    latest_child_mutation_at: int | float = Field(alias="latestChildMutationAt")
    reviewer_observed_at: int | float | None = Field(default=None, alias="reviewerObservedAt")
    reason: str

    @model_validator(mode="after")
    def _validate_freshness(self) -> Self:
        _validate_number(self.latest_child_mutation_at, "latestChildMutationAt")
        if self.reviewer_observed_at is not None:
            _validate_number(self.reviewer_observed_at, "reviewerObservedAt")
        _validate_public_value(self.reason)
        if self.satisfied and self.reviewer_observed_at is None:
            raise ValueError("satisfied reviewer freshness requires reviewer timestamp")
        if self.satisfied and self.reviewer_observed_at <= self.latest_child_mutation_at:
            raise ValueError("satisfied reviewer freshness must be strictly later")
        return self


class CodingChildConflictResolutionCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: CodingChildConflictResolutionCategory
    public_preview: str = Field(alias="publicPreview")
    resolution_state: CodingChildConflictResolutionState = Field(alias="resolutionState")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    conflict_metadata: CodingChildConflictMetadata = Field(alias="conflictMetadata")
    resolution_attempt: CodingChildConflictResolutionAttempt | None = Field(
        default=None,
        alias="resolutionAttempt",
    )
    disposition: CodingChildConflictDisposition | None = None
    reviewer_freshness: CodingChildReviewerFreshnessMetadata = Field(alias="reviewerFreshness")
    expected_conflict_cleared: bool = Field(alias="expectedConflictCleared")
    expected_resolution_reason: str | None = Field(
        default=None,
        alias="expectedResolutionReason",
    )
    expected_resolution_ignored_reason: str | None = Field(
        default=None,
        alias="expectedResolutionIgnoredReason",
    )
    attachment_flags: CodingChildConflictResolutionAttachmentFlags = Field(
        alias="attachmentFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.case_id.strip():
            raise ValueError("coding child conflict caseId must be non-empty")
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("coding child conflict cases require reasonCodes")
        _validate_public_value(self.public_preview)
        conflict_cleared, reason, ignored_reason = _derive_resolution(self)
        if conflict_cleared != self.expected_conflict_cleared:
            raise ValueError("expectedConflictCleared does not match recorded metadata")
        if reason != self.expected_resolution_reason:
            raise ValueError("expectedResolutionReason does not match recorded metadata")
        if ignored_reason != self.expected_resolution_ignored_reason:
            raise ValueError("expectedResolutionIgnoredReason does not match recorded metadata")
        if self.resolution_state == "blocking_conflict" and conflict_cleared:
            raise ValueError("blocking conflict state cannot be cleared")
        if self.resolution_state == "conflict_metadata_cleared" and not conflict_cleared:
            raise ValueError("cleared conflict state requires clearing metadata")
        self._validate_category_contract(conflict_cleared, reason, ignored_reason)
        return self

    def _validate_category_contract(
        self,
        conflict_cleared: bool,
        reason: str | None,
        ignored_reason: str | None,
    ) -> None:
        if self.category == "unresolved_conflict_blocks":
            if conflict_cleared or self.resolution_attempt is not None or self.disposition is not None:
                raise ValueError("unresolved conflict cannot include clearing metadata")
            if self.reviewer_freshness.required or self.reviewer_freshness.satisfied:
                raise ValueError("unresolved conflict does not evaluate reviewer freshness")
            if "child_worktree_conflict_unresolved" not in self.reason_codes:
                raise ValueError("unresolved conflict requires blocking reason")

        if self.category == "later_resolver_clears":
            if not conflict_cleared or reason != "later_conflict_resolver_covers_conflicted_files":
                raise ValueError("later resolver case must clear conflict via resolver metadata")
            if self.resolution_attempt is None:
                raise ValueError("later resolver case requires resolutionAttempt")

        if self.category == "same_spawn_reject_clears":
            if not conflict_cleared or reason != "same_spawn_reject":
                raise ValueError("same-spawn reject case must clear conflict")
            if self.disposition is None or self.disposition.action != "reject":
                raise ValueError("same-spawn reject case requires reject disposition")

        if self.category == "different_spawn_disposition_ignored":
            if conflict_cleared or ignored_reason != "disposition_spawn_mismatch":
                raise ValueError("different-spawn disposition must not clear conflict")
            if self.disposition is None:
                raise ValueError("different-spawn disposition case requires disposition")

        if self.category == "same_spawn_apply_requires_reviewer":
            if not conflict_cleared or reason != "same_spawn_apply":
                raise ValueError("same-spawn apply case must clear conflict metadata")
            _validate_apply_or_cherry_freshness(self, expected_action="apply")

        if self.category == "same_spawn_cherry_pick_requires_reviewer":
            if not conflict_cleared or reason != "same_spawn_cherry_pick":
                raise ValueError("same-spawn cherry-pick case must clear conflict metadata")
            _validate_apply_or_cherry_freshness(self, expected_action="cherry_pick")


class CodingChildConflictResolutionFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["codingChildConflictResolutionFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: CodingChildConflictResolutionAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    cases: tuple[CodingChildConflictResolutionCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("coding child conflict caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("coding child conflict fixture is missing required categories")
        return self


class CodingChildConflictResolutionProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: CodingChildConflictResolutionAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_resolution_state: dict[str, int] = Field(alias="byResolutionState")
    by_category: dict[str, int] = Field(alias="byCategory")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_coding_child_conflict_resolution_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> CodingChildConflictResolutionFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return CodingChildConflictResolutionFixture.model_validate(payload)


def project_coding_child_conflict_resolution_fixture(
    fixture: CodingChildConflictResolutionFixture | Mapping[str, Any],
) -> CodingChildConflictResolutionProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    for case in safe_fixture.cases:
        preview = _public_preview(case)
        public_previews[case.case_id] = preview
        snapshot = _case_snapshot(case, preview=preview)
        _validate_public_value(snapshot)
        case_snapshots[case.case_id] = snapshot
    return CodingChildConflictResolutionProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byResolutionState=dict(Counter(case.resolution_state for case in safe_fixture.cases)),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: CodingChildConflictResolutionFixture | Mapping[str, Any],
) -> CodingChildConflictResolutionFixture:
    if isinstance(fixture, CodingChildConflictResolutionFixture):
        return CodingChildConflictResolutionFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return CodingChildConflictResolutionFixture.model_validate(fixture)


def _case_snapshot(
    case: CodingChildConflictResolutionCase,
    *,
    preview: str,
) -> dict[str, object]:
    conflict_cleared, reason, ignored_reason = _derive_resolution(case)
    snapshot = {
        "caseId": case.case_id,
        "category": case.category,
        "publicPreview": preview,
        "resolutionState": case.resolution_state,
        "reasonCodes": case.reason_codes,
        "conflictCleared": conflict_cleared,
        "blocking": not conflict_cleared,
        "resolutionReason": reason,
        "resolutionIgnoredReason": ignored_reason,
        "conflictMetadata": case.conflict_metadata.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        "resolutionAttempt": (
            case.resolution_attempt.model_dump(by_alias=True, mode="json", warnings=False)
            if case.resolution_attempt is not None
            else None
        ),
        "disposition": (
            case.disposition.model_dump(by_alias=True, mode="json", warnings=False)
            if case.disposition is not None
            else None
        ),
        "requiresFreshReviewer": case.reviewer_freshness.required,
        "reviewerFreshnessSatisfied": case.reviewer_freshness.satisfied,
        "reviewerFreshness": case.reviewer_freshness.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        "adkRunnerInvoked": False,
        "childExecutionAttached": False,
        "toolHostDispatched": False,
        "gitExecuted": False,
        "shellOrCodeExecuted": False,
        "testExecuted": False,
        "fileMutated": False,
        "workspaceMutated": False,
        "liveAdoptionAttached": False,
        "routeOrApiAttached": False,
        "productionStorageWritten": False,
        "canaryTrafficAttached": False,
        "telegramAttached": False,
        "evidenceBlockEnabled": False,
    }
    _validate_public_value(snapshot)
    return snapshot


def _derive_resolution(
    case: CodingChildConflictResolutionCase,
) -> tuple[bool, str | None, str | None]:
    conflict = case.conflict_metadata
    if case.resolution_attempt is not None:
        covers_conflict = set(conflict.conflicted_files).issubset(
            set(case.resolution_attempt.covered_files)
        )
        is_later = case.resolution_attempt.spawn_started_at > conflict.spawn_observed_at
        if covers_conflict and is_later:
            return True, "later_conflict_resolver_covers_conflicted_files", None
        if not covers_conflict:
            return False, None, "resolver_file_coverage_mismatch"
        return False, None, "resolver_not_later_than_conflict"

    if case.disposition is not None:
        if case.disposition.spawn_ref != conflict.spawn_ref:
            return False, None, "disposition_spawn_mismatch"
        if case.disposition.result_index <= conflict.conflict_index:
            return False, None, "disposition_not_later_than_conflict"
        if case.disposition.action == "reject":
            return True, "same_spawn_reject", None
        if case.disposition.action == "apply":
            return True, "same_spawn_apply", None
        if case.disposition.action == "cherry_pick":
            return True, "same_spawn_cherry_pick", None

    return False, None, None


def _validate_apply_or_cherry_freshness(
    case: CodingChildConflictResolutionCase,
    *,
    expected_action: Literal["apply", "cherry_pick"],
) -> None:
    if case.disposition is None or case.disposition.action != expected_action:
        raise ValueError("apply/cherry-pick reviewer freshness case requires matching disposition")
    if case.disposition.spawn_ref != case.conflict_metadata.spawn_ref:
        raise ValueError("apply/cherry-pick disposition must target the conflicted spawn")
    freshness = case.reviewer_freshness
    if freshness.required is not True:
        raise ValueError("apply/cherry-pick conflict clearing requires reviewer freshness gate")
    if freshness.satisfied is not False:
        raise ValueError("fixture records reviewer freshness as separate unsatisfied metadata")
    if freshness.latest_child_mutation_at != case.conflict_metadata.latest_child_mutation_at:
        raise ValueError("reviewer freshness boundary must match latest child mutation")


def _public_preview(case: CodingChildConflictResolutionCase) -> str:
    redacted = sanitize_tool_preview(case.public_preview)
    redacted = _FORBIDDEN_PUBLIC_PATH_RE.sub("[redacted-path]", redacted)
    _validate_public_value(redacted)
    return redacted


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("coding child conflict fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("coding child conflict fixtures must be local and non-production")


def _reject_unsafe_relative_path(path_text: str) -> None:
    if not path_text.strip():
        raise ValueError("workspace paths must be non-empty")
    if path_text.startswith(("/", "\\")) or ".." in Path(path_text).parts:
        raise ValueError("workspace paths must be relative to the workspace")
    _reject_unsafe_path_text(path_text)


def _validate_ref_prefix(value: str, prefix: str) -> None:
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise ValueError(f"coding child conflict metadata refs must use {prefix} refs")
    if any(char.isspace() for char in value):
        raise ValueError("coding child conflict metadata refs must be compact")
    _validate_public_value(value)


def _validate_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _validate_public_value(value: object) -> None:
    if isinstance(value, str):
        if _FORBIDDEN_PUBLIC_PATH_RE.search(value):
            raise ValueError("coding child conflict public snapshot contains production paths")
        if _has_forbidden_public_token(value):
            raise ValueError("coding child conflict public snapshot contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("coding child conflict mappings must use string keys")
            _validate_public_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_public_value(item)
        return
    rendered = json.dumps(value, sort_keys=True)
    if _has_forbidden_public_token(rendered):
        raise ValueError("coding child conflict public snapshot contains unsafe data")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PUBLIC_PATH_RE.search(value):
            raise ValueError("coding child conflict fixture contains unsafe path")
        if _has_forbidden_public_token(value):
            raise ValueError("coding child conflict fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("coding child conflict mappings must use string keys")
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("coding child conflict fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("coding child conflict fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("coding child conflict mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("coding child conflict fixture values must be JSON-compatible")


def _has_forbidden_public_token(value: str) -> bool:
    casefolded = value.casefold()
    return any(token in casefolded for token in _FORBIDDEN_PUBLIC_TOKENS_NORMALIZED)


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    chars: list[str] = []
    previous_was_separator = False
    for char in value:
        if char.isalnum():
            chars.append(char.lower())
            previous_was_separator = False
        elif not previous_was_separator:
            chars.append("_")
            previous_was_separator = True
    return "".join(chars).strip("_")


__all__ = [
    "CodingChildConflictDisposition",
    "CodingChildConflictMetadata",
    "CodingChildConflictResolutionAttachmentFlags",
    "CodingChildConflictResolutionAttempt",
    "CodingChildConflictResolutionCase",
    "CodingChildConflictResolutionFixture",
    "CodingChildConflictResolutionProjection",
    "CodingChildReviewerFreshnessMetadata",
    "load_coding_child_conflict_resolution_fixture",
    "project_coding_child_conflict_resolution_fixture",
]
