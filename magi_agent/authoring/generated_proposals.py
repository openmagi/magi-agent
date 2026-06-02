from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self
from urllib.parse import unquote

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


GeneratedProposalExecutionDefault = Literal["denied"]

_DIGEST_PREFIX = "sha256:"
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
    revalidate_instances="always",
)
_PRIVATE_URI_SCHEMES = {
    "file",
    "gcs",
    "gs",
    "postgres",
    "postgresql",
    "s3",
    "supabase",
    "vault",
}
_RAW_IO_FIELD_NAMES = {
    "rawprompt",
    "rawmodelprompt",
    "prompttojson",
    "rawoutput",
    "rawmodeloutput",
    "modelrawoutput",
}
_RAW_CODE_FIELD_NAMES = {
    "code",
    "content",
    "contents",
    "filecontent",
    "filecontents",
    "rawcode",
    "generatedcode",
    "executablecode",
    "source",
    "sourcecode",
    "generatedsource",
}
_RAW_CREDENTIAL_FIELD_NAMES = {
    "apikey",
    "apitoken",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "credential",
    "credentials",
    "rawcredential",
    "rawcredentials",
    "password",
    "privatekey",
    "secret",
    "secrettoken",
    "token",
}
_RUNTIME_ENTRYPOINT_FIELD_NAMES = {
    "runtimeentrypoint",
    "entrypoint",
    "toolhostpath",
    "runtimepluginloader",
}
_ACTIVATION_FIELD_NAMES = {
    "activation",
    "activationenabled",
    "activationeligibility",
    "activationeligible",
    "activationplan",
    "activationready",
    "activated",
    "allowautoactivation",
    "autoactivation",
}
_MEMORY_AUTHORITY_FIELD_NAMES = {
    "allowmemorywrite",
    "memorywrite",
    "memorywriteallowed",
}
_WORKSPACE_AUTHORITY_FIELD_NAMES = {
    "allowworkspacemutation",
    "workspacemutation",
    "workspacemutationallowed",
}
_EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES = {
    "allowexternaldelivery",
    "externaldelivery",
    "externaldeliveryallowed",
}
_SCHEDULE_AUTHORITY_FIELD_NAMES = {
    "allowschedulemutation",
    "schedulemutation",
    "schedulemutationallowed",
    "cronmutation",
}
_LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES = {
    "allowliveconnectors",
    "liveconnectorcredentials",
    "connectorcredentialreadsallowed",
    "connectorcredentialsexposed",
    "connectorcredentials",
}
_SEPARATE_AGENT_IDENTITY_FIELD_NAMES = {
    "agentid",
    "builderagentid",
    "builderagentidentity",
    "builderagentref",
}
_URI_USERINFO_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/?#\s]*:[^/?#\s]*@")
_URI_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SIGNED_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|^)(?:x-amz-signature|x-amz-credential|x-goog-signature|"
    r"x-goog-credential|signature|sig|access_key|accesskey)="
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._-]{8,}|sk-(?:live|test)-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|apikey|token|secret|password|credential)\s*[:=?&]\s*[^\s,;]+)"
)
_RAW_MODEL_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"raw[\s_-]*model[\s_-]*output|raw[\s_-]*output|raw[\s_-]*prompt|"
    r"hidden[\s_-]*instructions?|hidden[\s_-]*transcript|"
    r"chain[\s_-]*of[\s_-]*thought|tool[\s_-]*result[\s_-]*payload"
    r")\b"
)
_PRIVATE_PATH_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"(?:/Users|/home|/root|/workspace|/app|/private|/var|/tmp|/etc|/opt|/srv|/mnt|~)"
    r"/[^\s,;)]+|"
    r"(?:\.\.?/)[^\s,;)]+|"
    r"(?:infra|apps|src|scripts|supabase|memory|outputs|tests|magi_agent|"
    r"\.claude|\.worktrees)/[^\s,;)]+|"
    r"[A-Za-z]:[\\/][^\s,;)]+"
    r")"
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_STATUS_TOKEN_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(?:active|activate|activated|activation|enabled|enable|live|promote|promoted|"
    r"promotion|runtime)"
    r"(?:$|[^a-z0-9])"
)
_SOURCE_CODE_TEXT_RE = re.compile(
    r"(?ims)(?:```|^#!|\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:(]|"
    r"\bfunction\s+\w*\s*\(|=>\s*\{|\b(?:import|from)\s+[\w.]+|"
    r"\b(?:console\.log|print)\s*\()"
)
_SEPARATE_BUILDER_AGENT_TEXT_RE = re.compile(r"(?i)\bbuilder[\s_.:-]*agent\b")
_AFFIRMATIVE_AUTHORITY_TEXT_RE = re.compile(
    r"(?is)\b(?:grant|grants|allow|allows|enable|enables|may|can|will|request|requests|"
    r"requested|write|mutate|create|deliver|access|use|read)\b(?!\s+no\b)"
    r".{0,80}\b"
    r"(?:live[\s_-]+connector[\s_-]+credentials?|connector[\s_-]+credentials?|"
    r"memory[\s_-]+write|workspace[\s_-]+mutation|external[\s_-]+delivery|"
    r"schedule[\s_-]+mutation|cron|webhook|"
    r"production\s+workspace)\b"
)
_AFFIRMATIVE_AUTHORITY_ACTION_RE = re.compile(
    r"(?is)\b(?:"
    r"(?:mutate|mutates|mutating)[\s_-]+(?:the[\s_-]+)?workspace|"
    r"(?:write|writes|writing)[\s_-]+(?:to[\s_-]+)?(?:the[\s_-]+)?memory|"
    r"(?:create|creates|creating)[\s_-]+(?:scheduled[\s_-]+jobs?|cron[\s_-]+jobs?|schedules?)|"
    r"(?:deliver|delivers|delivering)\b.{0,40}\bexternally|"
    r"externally\b.{0,40}\b(?:deliver|delivers|delivering)|"
    r"(?:access|accesses|use|uses|using|read|reads|reading)[\s_-]+"
    r"(?:live[\s_-]+)?connector[\s_-]+credentials?"
    r")\b"
)


class _GeneratedProposalModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for generated proposal contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _reject_unsafe_input_fields(cls, data: object) -> object:
        _reject_unsafe_input(data)
        return data


class GeneratedProposalSourceRef(_GeneratedProposalModel):
    ref: str
    digest: str

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "ref")

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "digest")


class GeneratedProposalArtifactFileRef(_GeneratedProposalModel):
    path: str
    digest: str
    byte_size: StrictInt = Field(ge=0, alias="byteSize")
    media_type: str = Field(alias="mediaType")
    executable: StrictBool = False

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _require_safe_artifact_path(value)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "digest")

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, value: str) -> str:
        if not value.strip() or "/" not in value or any(char.isspace() for char in value):
            raise ValueError("mediaType must be a concrete media type")
        _reject_unsafe_string(value, "mediaType")
        return value

    @model_validator(mode="after")
    def _require_non_executable(self) -> GeneratedProposalArtifactFileRef:
        _reject_true(self.executable, "executable")
        return self


class GeneratedProposalDigestSummaryRef(_GeneratedProposalModel):
    digest: str
    summary: str

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "digest")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_public_summary(value, "summary")


class GeneratedProposalSandboxPlanRef(_GeneratedProposalModel):
    ref: str
    digest: str
    summary: str

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "ref")

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "digest")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_public_summary(value, "summary")


class GeneratedProposalManifest(_GeneratedProposalModel):
    schema_version: Literal["generated_proposal_manifest.v1"] = Field(alias="schemaVersion")
    proposal_id: str = Field(alias="proposalId")
    source_draft_ref: GeneratedProposalSourceRef = Field(alias="sourceDraftRef")
    source_version_ref: GeneratedProposalSourceRef = Field(alias="sourceVersionRef")
    source_snapshot_ref: GeneratedProposalSourceRef = Field(alias="sourceSnapshotRef")
    artifacts: tuple[GeneratedProposalArtifactFileRef, ...] = Field(min_length=1)
    permission_manifest: GeneratedProposalDigestSummaryRef = Field(alias="permissionManifest")
    dependency_manifest: GeneratedProposalDigestSummaryRef = Field(alias="dependencyManifest")
    sandbox_plan: GeneratedProposalSandboxPlanRef = Field(alias="sandboxPlan")
    review_refs: tuple[str, ...] = Field(default=(), alias="reviewRefs")
    approval_refs: tuple[str, ...] = Field(default=(), alias="approvalRefs")
    execution_default: GeneratedProposalExecutionDefault = Field(
        default="denied", alias="executionDefault"
    )

    @field_validator("proposal_id")
    @classmethod
    def _validate_proposal_id(cls, value: str) -> str:
        return _require_safe_ref(value, "proposalId")

    @field_validator("review_refs", "approval_refs")
    @classmethod
    def _validate_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_safe_ref(value, "review/approval ref")
        return values

    @model_validator(mode="after")
    def _require_denied_execution_default(self) -> GeneratedProposalManifest:
        if self.execution_default != "denied":
            raise ValueError("executionDefault must be denied")
        return self


def digest_generated_proposal_manifest(
    manifest: GeneratedProposalManifest | Mapping[str, object],
) -> str:
    coerced = GeneratedProposalManifest.model_validate(_to_validation_payload(manifest))
    return _digest_json(_to_jsonable(coerced))


def _reject_unsafe_input(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = _normalize_field_name(key)
            if normalized in _RAW_CREDENTIAL_FIELD_NAMES:
                raise ValueError("raw credential fields are not accepted")
            if normalized in _RAW_IO_FIELD_NAMES:
                raise ValueError("raw prompt/output fields are not accepted")
            if normalized in _RAW_CODE_FIELD_NAMES:
                raise ValueError("raw generated code fields are not accepted")
            if normalized in _RUNTIME_ENTRYPOINT_FIELD_NAMES:
                raise ValueError("runtimeEntrypoint is not accepted in proposal manifests")
            if normalized in _LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES:
                raise ValueError("live connector credentials are not accepted")
            if normalized in _SEPARATE_AGENT_IDENTITY_FIELD_NAMES:
                raise ValueError("separate Builder Agent identity is not accepted")
            if normalized in _ACTIVATION_FIELD_NAMES:
                raise ValueError("activation flags are not accepted")
            if normalized in _MEMORY_AUTHORITY_FIELD_NAMES:
                raise ValueError("memory write authority is not accepted")
            if normalized in _WORKSPACE_AUTHORITY_FIELD_NAMES:
                raise ValueError("workspace mutation authority is not accepted")
            if normalized in _EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES:
                raise ValueError("external delivery authority is not accepted")
            if normalized in _SCHEDULE_AUTHORITY_FIELD_NAMES:
                raise ValueError("schedule mutation authority is not accepted")
            if normalized == "executable" and nested is True:
                raise ValueError("executable cannot be true in proposal manifests")
            _reject_unsafe_input(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_unsafe_input(nested)


def _require_safe_artifact_path(value: str) -> str:
    for candidate in _decoded_candidates(value):
        if not candidate.strip():
            raise ValueError("path must be non-empty")
        if "\\" in candidate:
            raise ValueError("path must not contain backslashes")
        if _URI_SCHEME_RE.match(candidate):
            raise ValueError("path must be a relative artifact path")
        if candidate.startswith(("/", "~")):
            raise ValueError("path must be a public relative artifact path")
        if _WINDOWS_DRIVE_RE.match(candidate):
            raise ValueError("path must not be a Windows drive path")
        parts = candidate.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path must not contain traversal or symlink escape segments")
        _reject_unsafe_string(candidate, "path")
    return value


def _require_safe_ref(value: str, field_name: str) -> str:
    for candidate in _decoded_candidates(value):
        if not candidate.strip():
            raise ValueError(f"{field_name} must be non-empty")
        if "\\" in candidate:
            raise ValueError(f"{field_name} must not contain private path separators")
        if (
            candidate.startswith(("/", "~", "../", "./"))
            or "/../" in candidate
            or candidate.endswith("/..")
            or "/./" in candidate
            or candidate.endswith("/.")
        ):
            raise ValueError(f"{field_name} must not contain private paths")
        if _WINDOWS_DRIVE_RE.match(candidate):
            raise ValueError(f"{field_name} must not be a Windows drive path")
        _reject_status_tokens(candidate, field_name)
        _reject_unsafe_string(candidate, field_name)
    return value


def _require_public_summary(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_string(value, field_name)
    return value


def _reject_unsafe_string(value: str, field_name: str) -> None:
    for candidate in _decoded_candidates(value):
        lowered = candidate.lower()
        if _URI_USERINFO_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _SIGNED_QUERY_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _SECRET_TEXT_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _RAW_MODEL_TEXT_RE.search(candidate):
            raise ValueError(f"raw model data is not accepted in {field_name}")
        if _SOURCE_CODE_TEXT_RE.search(candidate):
            raise ValueError(f"raw source code is not accepted in {field_name}")
        if _SEPARATE_BUILDER_AGENT_TEXT_RE.search(candidate):
            raise ValueError(f"separate Builder Agent identity is not accepted in {field_name}")
        _reject_authority_text(candidate, field_name)
        if _is_private_scheme(lowered):
            raise ValueError(f"private URI schemes are not accepted in {field_name}")
        if _PRIVATE_PATH_TEXT_RE.search(candidate):
            raise ValueError(f"private paths are not accepted in {field_name}")


def _is_private_scheme(lowered: str) -> bool:
    match = _URI_SCHEME_RE.match(lowered)
    return bool(match and match.group(1) in _PRIVATE_URI_SCHEMES)


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_DIGEST_PREFIX}{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _to_jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_jsonable(nested) for nested in value]
    return value


def _to_validation_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        payload: dict[str, object] = {}
        for name, field in type(value).model_fields.items():
            if not hasattr(value, name):
                continue
            alias = field.alias or name
            payload[alias] = _to_validation_payload(getattr(value, name))
        return payload
    if isinstance(value, Mapping):
        return {str(key): _to_validation_payload(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_validation_payload(nested) for nested in value]
    return value


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _decoded_candidates(value: str) -> tuple[str, ...]:
    if len(value) > 2048:
        raise ValueError("encoded values are too large to validate")
    candidates = [value]
    decoded = value
    for _ in range(20):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
        candidates.append(decoded)
    else:
        raise ValueError("percent-encoded values must decode to a stable value")
    return tuple(dict.fromkeys(candidates))


def _reject_status_tokens(value: str, field_name: str) -> None:
    if _STATUS_TOKEN_RE.search(value):
        raise ValueError(f"activation/runtime status tokens are not accepted in {field_name}")


def _reject_authority_text(value: str, field_name: str) -> None:
    if _AFFIRMATIVE_AUTHORITY_TEXT_RE.search(value) or _AFFIRMATIVE_AUTHORITY_ACTION_RE.search(
        value
    ):
        raise ValueError(f"authoring authority is not accepted in {field_name}")


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _reject_true(value: bool, alias: str) -> None:
    if value:
        raise ValueError(f"{alias} cannot be true in generated proposal contracts")


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


__all__ = [
    "GeneratedProposalArtifactFileRef",
    "GeneratedProposalDigestSummaryRef",
    "GeneratedProposalExecutionDefault",
    "GeneratedProposalManifest",
    "GeneratedProposalSandboxPlanRef",
    "GeneratedProposalSourceRef",
    "digest_generated_proposal_manifest",
]
