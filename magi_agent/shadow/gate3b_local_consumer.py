from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate3b_bundle import Gate3BLiveDuplicateBundle
from magi_agent.shadow.gate3b_ingest import (
    convert_gate3b_live_duplicate_to_gate3a_recorded_bundle,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_ISOLATED_DIR_SEGMENTS = frozenset({"adk-shadow-capture", "gate3b-shadow-capture"})
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"(?:^|[\\/])[.]kube(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|"
    r"(?:^|[\\/])(?:db|database)(?:[\\/]|$)|"
    r"infra[\\/]k8s|infra[\\/]docker[\\/]provisioning-worker|deploy(?:ment)?[\\/]|"
    r"deploy\.sh|runtime-selector|runtime_selector|"
    r"(?:^|[\\/])(?:missions?|schedulers?)(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:mission|scheduler)-store(?:[\\/]|$)",
    re.IGNORECASE,
)
_URI_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class Gate3BLocalConsumerError(ValueError):
    pass


class Gate3BLocalConsumerAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_shadow_executed: Literal[False] = Field(default=False, alias="liveShadowExecuted")
    tools_executed: Literal[False] = Field(default=False, alias="toolsExecuted")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    public_output_attached: Literal[False] = Field(
        default=False,
        alias="publicOutputAttached",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_queue_enqueued: Literal[False] = Field(
        default=False,
        alias="productionQueueEnqueued",
    )
    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{key: False for key in cls.model_fields})

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(by_alias=True, mode="python"))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {key: False for key in cls.model_fields}

    @field_serializer(
        "adk_runner_invoked",
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "public_output_attached",
        "production_storage_written",
        "production_queue_enqueued",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate3BLocalConsumerConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    input_dir: Path | None = None
    max_files: int = Field(default=100, ge=1)
    max_total_bytes: int = Field(default=10_485_760, ge=1)
    max_bundle_bytes: int = Field(default=262_144, ge=1)
    processed_bundle_ids: tuple[str, ...] = Field(default=(), alias="processedBundleIds")


Gate3BLocalSkipReason = Literal[
    "file_limit_exceeded",
    "file_too_large",
    "total_bytes_exceeded",
    "invalid_json",
    "validation_failed",
    "duplicate_bundle_id",
    "symlink_not_allowed",
    "not_a_file",
]


class Gate3BLocalSkippedFile(BaseModel):
    model_config = _MODEL_CONFIG

    path: Path
    reason: Gate3BLocalSkipReason
    message: str = ""


class Gate3BLocalConsumedBundle(BaseModel):
    model_config = _MODEL_CONFIG

    bundle_id: str = Field(alias="bundleId")
    path: Path
    source_path: str = Field(alias="sourcePath")
    file_size_bytes: int = Field(alias="fileSizeBytes")
    consumed_at: datetime = Field(alias="consumedAt")
    handoff_mode: Literal["gate3b_local_file_to_gate3a_recorded_handoff"] = Field(
        alias="handoffMode",
    )
    recorded_bundle_payload: dict[str, object] = Field(alias="recordedBundlePayload")
    handoff_metadata: dict[str, object] = Field(alias="handoffMetadata")


class Gate3BLocalConsumerResult(BaseModel):
    model_config = _MODEL_CONFIG

    consumed: tuple[Gate3BLocalConsumedBundle, ...] = ()
    skipped: tuple[Gate3BLocalSkippedFile, ...] = ()
    attachment_flags: Gate3BLocalConsumerAttachmentFlags = Field(
        default_factory=Gate3BLocalConsumerAttachmentFlags,
        alias="attachmentFlags",
    )


def consume_gate3b_local_files(
    config: Gate3BLocalConsumerConfig,
) -> Gate3BLocalConsumerResult:
    if not config.enabled:
        return Gate3BLocalConsumerResult()
    if config.input_dir is None:
        raise Gate3BLocalConsumerError("Gate 3B local consumer input_dir is required")

    input_dir = validate_gate3b_local_consumer_path(config.input_dir)
    if not input_dir.is_dir():
        raise Gate3BLocalConsumerError("Gate 3B local consumer input_dir must be a directory")

    consumed: list[Gate3BLocalConsumedBundle] = []
    skipped: list[Gate3BLocalSkippedFile] = []
    seen_bundle_ids = set(config.processed_bundle_ids)
    total_bytes = 0

    candidates = sorted(
        _iter_candidate_files(input_dir),
        key=lambda item: (item.stat.st_mtime_ns, item.path.name),
    )
    for index, candidate in enumerate(candidates):
        if index >= config.max_files:
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="file_limit_exceeded",
                    message="Gate 3B local file limit exceeded",
                )
            )
            continue
        if candidate.path.is_symlink():
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="symlink_not_allowed",
                    message="Gate 3B local consumer does not follow symlinked bundles",
                )
            )
            continue
        if not candidate.path.is_file():
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="not_a_file",
                    message="Gate 3B local consumer accepts JSON files only",
                )
            )
            continue
        file_size = candidate.stat.st_size
        if file_size > config.max_bundle_bytes:
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="file_too_large",
                    message="Gate 3B local bundle file exceeded max_bundle_bytes",
                )
            )
            continue
        if total_bytes + file_size > config.max_total_bytes:
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="total_bytes_exceeded",
                    message="Gate 3B local consumer total byte limit exceeded",
                )
            )
            continue

        total_bytes += file_size

        try:
            payload = _read_json(candidate.path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="invalid_json",
                    message="Gate 3B local bundle JSON is malformed or partial",
                )
            )
            continue

        try:
            bundle = Gate3BLiveDuplicateBundle.model_validate(payload)
        except Exception:
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="validation_failed",
                    message="Gate 3B local bundle failed redacted schema validation",
                )
            )
            continue

        if bundle.bundle_id in seen_bundle_ids:
            skipped.append(
                Gate3BLocalSkippedFile(
                    path=candidate.path,
                    reason="duplicate_bundle_id",
                    message="Gate 3B local bundle ID was already consumed",
                )
            )
            continue

        handoff = convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(bundle)
        handoff_metadata = {
            **handoff.handoff_metadata,
            "handoffMode": "gate3b_local_file_to_gate3a_recorded_handoff",
            "sourcePath": candidate.path.name,
            "fileSizeBytes": file_size,
            "liveShadowExecuted": False,
            "userVisibleOutputAttached": False,
            "storageWritten": False,
            "queueEnqueued": False,
        }
        consumed.append(
            Gate3BLocalConsumedBundle(
                bundleId=bundle.bundle_id,
                path=candidate.path,
                sourcePath=candidate.path.name,
                fileSizeBytes=file_size,
                consumedAt=datetime.now(UTC),
                handoffMode="gate3b_local_file_to_gate3a_recorded_handoff",
                recordedBundlePayload=handoff.recorded_bundle_payload,
                handoffMetadata=handoff_metadata,
            )
        )
        seen_bundle_ids.add(bundle.bundle_id)

    return Gate3BLocalConsumerResult(consumed=tuple(consumed), skipped=tuple(skipped))


def validate_gate3b_local_consumer_path(path: Path | str) -> Path:
    raw = str(path)
    _reject_unsafe_path_text(raw)
    candidate = Path(path)
    if not candidate.is_absolute():
        raise Gate3BLocalConsumerError("Gate 3B local consumer path must be absolute")
    if candidate.is_symlink():
        raise Gate3BLocalConsumerError("Gate 3B local consumer path must not be a symlink")
    resolved = candidate.resolve(strict=False)
    _reject_unsafe_path_text(str(resolved))
    _require_isolated_capture_segment(resolved)
    return resolved


class _CandidateFile(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    path: Path
    stat: object


def _iter_candidate_files(input_dir: Path) -> tuple[_CandidateFile, ...]:
    candidates: list[_CandidateFile] = []
    for child in input_dir.iterdir():
        if child.suffix != ".json":
            continue
        candidates.append(_CandidateFile(path=child, stat=child.lstat()))
    return tuple(candidates)


def _read_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _reject_unsafe_path_text(path_text: str) -> None:
    if ".." in Path(path_text).parts:
        raise Gate3BLocalConsumerError("Gate 3B local consumer path must not traverse parents")
    if _URI_SCHEME_RE.search(path_text) or _PRODUCTION_PATH_RE.search(path_text):
        raise Gate3BLocalConsumerError("Gate 3B local consumer path must be local and isolated")


def _require_isolated_capture_segment(path: Path) -> None:
    parts = set(path.parts)
    if parts & _ISOLATED_DIR_SEGMENTS:
        return
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    if path.is_relative_to(temp_root) and path.name in _ISOLATED_DIR_SEGMENTS:
        return
    raise Gate3BLocalConsumerError(
        "Gate 3B local consumer path must include adk-shadow-capture "
        "or gate3b-shadow-capture",
    )


__all__ = [
    "Gate3BLocalConsumedBundle",
    "Gate3BLocalConsumerAttachmentFlags",
    "Gate3BLocalConsumerConfig",
    "Gate3BLocalConsumerError",
    "Gate3BLocalConsumerResult",
    "Gate3BLocalSkippedFile",
    "consume_gate3b_local_files",
    "validate_gate3b_local_consumer_path",
]
