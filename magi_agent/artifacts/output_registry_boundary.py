from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import re
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


OutputArtifactRegistryOperation = Literal[
    "artifact.create",
    "artifact.read",
    "artifact.list",
    "artifact.update",
    "artifact.delete",
    "artifact.import_child",
]
OutputArtifactRegistryStatus = Literal[
    "disabled",
    "intent",
    "recorded_local_fake",
    "blocked",
    "error",
]
OutputArtifactFormat = Literal[
    "markdown",
    "txt",
    "html",
    "pdf",
    "docx",
    "hwpx",
    "xlsx",
    "csv",
    "tsv",
    "json",
    "binary",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test|artifact)?[-_A-Za-z0-9]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+|s3://[^,\s\"']+|"
    r"gs://[^,\s\"']+|supabase://[^,\s\"']+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|browser|child)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"reasoning[_ -]?trace|model[_ -]?internal|authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY_MARKERS = (
    "raw",
    "secret",
    "token",
    "credential",
    "password",
    "cookie",
    "path",
    "hidden",
    "transcript",
    "prompt",
    "tool",
    "child",
)


class OutputArtifactRegistryProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def execute(self, request: OutputArtifactRegistryRequest) -> Mapping[str, object]: ...


class OutputArtifactRegistryConfig(FalseOnlyAuthorityModel):
    enabled: bool = False
    local_fake_registry_enabled: bool = Field(
        default=False,
        alias="localFakeRegistryEnabled",
    )
    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    production_storage_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionStorageWritesEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class OutputArtifactAuthorityFlags(FalseOnlyAuthorityModel):
    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    artifact_written: Literal[False] = Field(default=False, alias="artifactWritten")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    child_artifact_imported: Literal[False] = Field(default=False, alias="childArtifactImported")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class OutputArtifactRecord(BaseModel):
    model_config = _MODEL_CONFIG

    artifact_id: str = Field(alias="artifactId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    title: str
    filename: str
    format: OutputArtifactFormat
    content_digest: str = Field(alias="contentDigest")
    artifact_ref: str = Field(alias="artifactRef")
    generated_output_path_preview: str = Field(alias="generatedOutputPathPreview")
    provenance_refs: tuple[str, ...] = Field(default=(), alias="provenanceRefs")
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    provider_record_id: str | None = Field(default=None, alias="providerRecordId")

    @field_validator("artifact_id", "session_id", "turn_id", "artifact_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("title")
    @classmethod
    def _sanitize_title(cls, value: str) -> str:
        clean = _safe_text(value)
        if not clean:
            raise ValueError("artifact title must be non-empty")
        return clean[:160]

    @field_validator("filename")
    @classmethod
    def _sanitize_filename(cls, value: str) -> str:
        return _safe_filename(value)

    @field_validator("content_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("contentDigest must be sha256")
        return value

    @field_validator("generated_output_path_preview")
    @classmethod
    def _validate_generated_path_preview(cls, value: str) -> str:
        path = PurePosixPath(value)
        parts = path.parts
        if path.is_absolute() or ".." in parts or not str(value).startswith("outputs/"):
            raise ValueError("generatedOutputPathPreview must be safe relative preview")
        return str(path)

    @field_validator("provenance_refs", "source_refs")
    @classmethod
    def _sanitize_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_public_ref(item, "ref") for item in value)

    def public_projection(self) -> dict[str, object]:
        return {
            "artifactId": _public_ref(self.artifact_id, "artifact"),
            "sessionId": _public_ref(self.session_id, "session"),
            "turnId": _public_ref(self.turn_id, "turn"),
            "title": _safe_text(self.title)[:160],
            "filename": _public_filename(self.filename),
            "format": self.format,
            "contentDigest": self.content_digest,
            "artifactRef": _public_ref(self.artifact_ref, "artifact"),
            "generatedOutputPathPreview": self.generated_output_path_preview,
            "provenanceRefs": [_public_ref(ref, "provenance") for ref in self.provenance_refs],
            "sourceRefs": [_public_ref(ref, "source") for ref in self.source_refs],
            "providerRecordId": (
                None if self.provider_record_id is None else _public_ref(self.provider_record_id, "provider")
            ),
        }


class OutputArtifactRegistryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: OutputArtifactRegistryOperation
    request_id: str = Field(alias="requestId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    artifact_id: str | None = Field(default=None, alias="artifactId")
    title: str | None = None
    filename: str | None = None
    format: OutputArtifactFormat | None = None
    content_digest: str | None = Field(default=None, alias="contentDigest")
    child_artifact_ref: str | None = Field(default=None, alias="childArtifactRef")
    metadata: Mapping[str, object] = Field(default_factory=dict)


class OutputArtifactRegistryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: OutputArtifactRegistryStatus
    operation: OutputArtifactRegistryOperation
    record: OutputArtifactRecord | None = None
    records: tuple[OutputArtifactRecord, ...] = ()
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: OutputArtifactAuthorityFlags = Field(
        default_factory=OutputArtifactAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = OutputArtifactAuthorityFlags()
        return cls.model_validate(values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "record": None if self.record is None else self.record.public_projection(),
            "records": [record.public_projection() for record in self.records],
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class OutputArtifactRegistryBoundary:
    """Default-off OutputArtifactRegistry/ArtifactManager lifecycle boundary."""

    def __init__(self, config: OutputArtifactRegistryConfig) -> None:
        self.config = config

    async def execute(
        self,
        request: OutputArtifactRegistryRequest,
        *,
        provider: OutputArtifactRegistryProviderPort | None = None,
    ) -> OutputArtifactRegistryDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeRegistryEnabled": self.config.local_fake_registry_enabled,
            "productionStorageWritesEnabled": False,
            "adkArtifactServiceAttached": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        validation_error = _validate_request(request)
        if not self.config.enabled:
            return _decision(request, "disabled", ("output_artifact_registry_disabled",), diagnostics)
        if validation_error is not None:
            return _decision(request, "blocked", (validation_error,), diagnostics)
        if not self.config.local_fake_registry_enabled or provider is None:
            record = _local_record(request, provider_raw=None)
            return _decision(
                request,
                "intent",
                ("local_fake_registry_provider_disabled",),
                diagnostics,
                record=record,
            )
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return _decision(request, "blocked", ("local_fake_registry_provider_untrusted",), diagnostics)
        try:
            raw = provider.execute(request)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:
            return _decision(
                request,
                "error",
                ("local_fake_registry_provider_error",),
                {**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        raw_status = str(raw.get("status") or "ok")
        record = _local_record(request, provider_raw=raw)
        if raw_status not in {"ok", "found", "listed", "updated", "deleted"}:
            return _decision(
                request,
                "blocked",
                ("output_registry_ack_failed",),
                diagnostics,
                record=record,
            )
        return _decision(
            request,
            "recorded_local_fake",
            (f"{request.operation.replace('.', '_')}_local_fake_receipt_only",),
            diagnostics,
            record=record,
            records=(record,) if request.operation == "artifact.list" else (),
        )


def _decision(
    request: OutputArtifactRegistryRequest,
    status: OutputArtifactRegistryStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    record: OutputArtifactRecord | None = None,
    records: tuple[OutputArtifactRecord, ...] = (),
) -> OutputArtifactRegistryDecision:
    return OutputArtifactRegistryDecision(
        status=status,
        operation=request.operation,
        record=record,
        records=records,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=OutputArtifactAuthorityFlags(),
    )


def _validate_request(request: OutputArtifactRegistryRequest) -> str | None:
    text_fields = (request.title or "", request.filename or "", request.child_artifact_ref or "")
    if any(_contains_private_payload(value) for value in text_fields):
        if request.filename is not None and _unsafe_filename(request.filename):
            return "unsafe_filename_blocked"
        return "private_artifact_payload_blocked"
    if request.operation in {"artifact.create", "artifact.update", "artifact.import_child"}:
        if not request.title:
            return "artifact_title_required"
        if not request.filename:
            return "artifact_filename_required"
        if _unsafe_filename(request.filename):
            return "unsafe_filename_blocked"
        if not request.format:
            return "artifact_format_required"
        if request.content_digest is None or not _SHA256_RE.fullmatch(request.content_digest):
            return "content_digest_required"
    if request.operation in {"artifact.read", "artifact.update", "artifact.delete"} and not request.artifact_id:
        return "artifact_id_required"
    if request.operation == "artifact.import_child" and not request.child_artifact_ref:
        return "child_artifact_ref_required"
    return None


def _local_record(
    request: OutputArtifactRegistryRequest,
    *,
    provider_raw: Mapping[str, object] | None,
) -> OutputArtifactRecord:
    artifact_id = request.artifact_id or _artifact_id(request)
    filename = _safe_filename(request.filename or "artifact.bin")
    provider_ref = None if provider_raw is None else provider_raw.get("artifactRef")
    provenance_refs: tuple[str, ...] = ()
    if request.operation == "artifact.import_child" and request.child_artifact_ref is not None:
        provenance_refs = (_public_ref(request.child_artifact_ref, "child-artifact"),)
    return OutputArtifactRecord(
        artifactId=artifact_id,
        sessionId=request.session_id,
        turnId=request.turn_id,
        title=request.title or "Artifact",
        filename=filename,
        format=request.format or "binary",
        contentDigest=request.content_digest or _empty_digest(),
        artifactRef=_public_ref(str(provider_ref or artifact_id), "artifact"),
        generatedOutputPathPreview=_generated_output_path_preview(request.session_id, filename),
        provenanceRefs=provenance_refs,
        sourceRefs=(),
        providerRecordId=(
            None
            if provider_raw is None or provider_raw.get("providerRecordId") is None
            else str(provider_raw.get("providerRecordId"))
        ),
    )


def _artifact_id(request: OutputArtifactRegistryRequest) -> str:
    seed = f"{request.session_id}:{request.turn_id}:{request.operation}:{request.filename}"
    return f"artifact:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _empty_digest() -> str:
    return "sha256:" + hashlib.sha256(b"").hexdigest()


def _generated_output_path_preview(session_id: str, filename: str) -> str:
    session_hash = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:16]
    return str(PurePosixPath("outputs", f"session-{session_hash}", _safe_filename(filename)))


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[str(key)] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_ref(value: str) -> str:
    clean = _safe_text(str(value))
    if not clean:
        raise ValueError("reference must be non-empty")
    return clean[:180]


def _public_ref(value: str, prefix: str) -> str:
    clean = _safe_text(str(value))
    if _REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _safe_filename(value: str) -> str:
    if _unsafe_filename(value):
        raise ValueError("filename must be a public basename")
    clean = _safe_text(value)
    if not clean:
        raise ValueError("filename must be non-empty")
    return PurePosixPath(clean).name[:160]


def _public_filename(value: str) -> str:
    try:
        return _safe_filename(value)
    except ValueError:
        return f"artifact-{hashlib.sha1(value.encode('utf-8')).hexdigest()[:12]}.bin"


def _unsafe_filename(value: str) -> bool:
    return (
        value.startswith(("/", "~"))
        or "\\" in value
        or ".." in PurePosixPath(value).parts
        or PurePosixPath(value).name != value
        or bool(_PRIVATE_PATH_RE.search(value))
    )


def _safe_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None and not _PRIVATE_PATH_RE.search(line)
    ]
    clean = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


def _contains_private_payload(value: str) -> bool:
    return bool(_RAW_PRIVATE_LINE_RE.search(value) or _PRIVATE_PATH_RE.search(value) or _SECRET_TEXT_RE.search(value))


def _safe_provider_error(exc: BaseException) -> str:
    return _safe_text(str(exc))[:240] or "[redacted-provider-error]"


__all__ = [
    "OutputArtifactAuthorityFlags",
    "OutputArtifactRecord",
    "OutputArtifactRegistryBoundary",
    "OutputArtifactRegistryConfig",
    "OutputArtifactRegistryDecision",
    "OutputArtifactRegistryRequest",
]
