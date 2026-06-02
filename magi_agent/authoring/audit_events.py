from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from html import unescape
from typing import Literal, Self
from unicodedata import category as unicode_category
from unicodedata import normalize as unicode_normalize
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


RecipeBuilderAuditEventType = Literal[
    "recipe_builder_mode_entered",
    "recipe_builder_mode_exited",
    "recipe_builder_mode_expired",
    "draft_saved",
    "compile_started",
    "compile_completed",
    "dry_run_completed",
    "eval_completed",
    "approval_requested",
    "approval_decision_recorded",
    "promotion_requested",
    "promotion_applied",
    "activation_blocked",
    "runtime_admission_changed",
    "generated_plugin_proposal_created",
    "generated_plugin_sandbox_started",
    "generated_plugin_sandbox_completed",
    "export_created",
    "import_validated",
    "storage_error",
]
RecipeBuilderAuditRedactionStatus = Literal["redacted", "digest_only", "not_applicable"]
RecipeBuilderAuditValidationStatus = Literal["valid"]
RecipeBuilderAuditValidationMode = Literal["validate_only"]

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
    "secretkey",
    "secrettoken",
    "token",
}
_RUNTIME_ENTRYPOINT_FIELD_NAMES = {
    "runtimeentrypoint",
    "entrypoint",
    "pluginloader",
}
_ACTIVATION_FIELD_NAMES = {
    "activation",
    "activationeligibility",
    "activationeligible",
    "activationplan",
    "activationready",
    "activated",
    "allowautoactivation",
    "autoactivation",
    "activationenabled",
    "runtimeactivationeligible",
}
_MEMORY_AUTHORITY_FIELD_NAMES = {
    "allowmemorywrite",
    "memorywrite",
    "memorywriteallowed",
    "memorywriteenabled",
    "memorywritesenabled",
}
_WORKSPACE_AUTHORITY_FIELD_NAMES = {
    "allowworkspacemutation",
    "workspacemutation",
    "workspacemutationallowed",
    "workspacemutationenabled",
}
_EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES = {
    "allowexternaldelivery",
    "externaldelivery",
    "externaldeliveryallowed",
    "externaldeliveryenabled",
}
_SCHEDULE_AUTHORITY_FIELD_NAMES = {
    "allowschedulemutation",
    "schedulemutation",
    "schedulemutationallowed",
    "schedulemutationenabled",
    "schedulemutation",
    "cronmutation",
    "schedulesrestored",
}
_LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES = {
    "allowliveconnectors",
    "liveconnectorcredentials",
    "connectorcredentialreadsallowed",
    "connectorcredentialsexposed",
    "connectorcredentials",
    "connectorcredentialsaccessed",
    "connectorcredentialsrestored",
}
_SEPARATE_AGENT_IDENTITY_FIELD_NAMES = {
    "agentid",
    "builderagentid",
    "builderagentidentity",
    "builderagentref",
}
_WRITE_APPLY_FIELD_NAMES = {
    "apply",
    "applyevent",
    "promote",
    "promotetolive",
    "activate",
    "activatelive",
    "live",
    "livemode",
}
_URI_USERINFO_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/?#\s]*:[^/?#\s]*@")
_URI_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SIGNED_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|^)(?:x-amz-signature|x-amz-credential|x-goog-signature|"
    r"x-goog-credential|signature|sig|access_key|accesskey)="
)
_ENCODED_CONTROL_TEXT_RE = re.compile(
    r"(?i)(?:&#x0*(?:[0-8bcef]|1[0-9a-f]|7f);|"
    r"&#0*(?:[0-8]|1[0-9]|2[0-9]|3[01]|127);)"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----|"
    r"bearer\s+[A-Za-z0-9._-]{8,}|sk-(?:live|test)-[A-Za-z0-9_-]{8,}|"
    r"(?:api[\s_./:+\\-]*key|apikey|private[\s_./:+\\-]*key|"
    r"secret[\s_./:+\\-]*key|secret[\s_./:+\\-]*access[\s_./:+\\-]*key|"
    r"access[\s_./:+\\-]*key|token|secret|password|"
    r"credentials?)[\"']?(?:\s*[:=?&]\s*|\s+)[\"']?[A-Za-z0-9._/@+=-]{6,}[\"']?)"
)
_RAW_MODEL_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"raw[\s_-]*model[\s_-]*output|raw[\s_-]*output|raw[\s_-]*prompt|"
    r"hidden[\s_-]*instructions?|hidden[\s_-]*transcript|"
    r"chain[\s_-]*of[\s_-]*thought|tool[\s_-]*result[\s_-]*payload"
    r")\b"
)
_RAW_PAYLOAD_LABEL_RE = re.compile(
    r"(?is)(?=\b([a-z][^\n\r:=]{0,96}?)(?::\s*|=\s*)\S)"
)
_RAW_PAYLOAD_LABEL_FIELD_NAMES = _RAW_IO_FIELD_NAMES | _RAW_CODE_FIELD_NAMES | {
    "modeloutput",
    "file",
    "output",
    "prompt",
}
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
_SOURCE_CODE_TEXT_RE = re.compile(
    r"(?ms)(?:```|^\s*#!|\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:(]|"
    r"\bfunction\s+\w*\s*\(|=>\s*\{|^\s*(?:import|from)\s+[\w.]+|"
    r"\b(?:console\.log|print)\s*\()"
)
_SEPARATE_BUILDER_AGENT_TEXT_RE = re.compile(r"(?i)\bbuilder[\s_.:-]*agent\b")
_URI_SCHEME_ANYWHERE_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9+.-])([a-z][a-z0-9+.-]*):"
)
_BASE64_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_+/\-])([A-Za-z0-9_+/\-]{8,}={0,2})(?![A-Za-z0-9_+/\-])"
)
_BASE64_SPLIT_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9_+/\-])"
    r"([A-Za-z0-9_+/\-]+=*(?:\s+[A-Za-z0-9_+/\-]+=*)+)"
    r"(?![A-Za-z0-9_+/\-])"
)
_BASE64_SHORT_URLSAFE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_+/\-])([A-Za-z0-9_-]{4,7})(?![A-Za-z0-9_+/\-])"
)
_AFFIRMATIVE_AUTHORITY_TEXT_RE = re.compile(
    r"(?is)\b(?:grant|grants|allow|allows|enable|enables|may|can|will|request|requests|"
    r"requested|write|mutate|create|deliver|access|use|read|restore|restores|restored|"
    r"apply|applies|applied|promote|promotes|promoted|activate|activates|activated)\b"
    r"(?!\s+no\b).{0,80}\b"
    r"(?:live[\s_-]+connector[\s_-]+credentials?|connector[\s_-]+credentials?|"
    r"memory[\s_-]+write|workspace[\s_-]+mutation|external[\s_-]+delivery|"
    r"schedule[\s_-]+mutation|cron|webhook|production\s+workspace|live[\s_-]+mode)\b"
)
_AFFIRMATIVE_AUTHORITY_ACTION_RE = re.compile(
    r"(?is)\b(?:"
    r"(?:mutate|mutates|mutating)[\s_-]+(?:the[\s_-]+)?workspace|"
    r"(?:write|writes|writing)[\s_-]+(?:to[\s_-]+)?(?:the[\s_-]+)?memory|"
    r"(?:create|creates|creating)[\s_-]+(?:scheduled[\s_-]+jobs?|cron[\s_-]+jobs?|schedules?)|"
    r"(?:deliver|delivers|delivering)\b.{0,40}\bexternally|"
    r"externally\b.{0,40}\b(?:deliver|delivers|delivering)|"
    r"(?:access|accesses|use|uses|using|read|reads|reading)[\s_-]+"
    r"(?:live[\s_-]+)?connector[\s_-]+credentials?|"
    r"(?:enable|enables|enabled)[\s_-]+live[\s_-]+mode"
    r")\b"
)
_NOUN_FIRST_AUTHORITY_TEXT_RE = re.compile(
    r"(?is)\b(?:"
    r"memory[\s_-]+writes?|workspace[\s_-]+mutations?|external[\s_-]+deliver(?:y|ies)|"
    r"schedule[\s_-]+mutation|schedules?|cron|webhooks?|connector[\s_-]+credentials?|"
    r"restore|apply|promotions?[\s_-]+to[\s_-]+live|promote[\s_-]+to[\s_-]+live|"
    r"activations?|runtime[\s_-]+activations?|live[\s_-]+mode"
    r")\b.{0,80}\b(?:enabled|allowed|applied|promoted|activated|eligible|restored)\b"
)
_TEXT_FIELD_ASSIGNMENT_RE = re.compile(
    r"(?is)\b([a-z][a-z0-9\s_./:+\\-]{0,48})[\"']?\s*(?::|=)\s*"
    r"[\"']?(true|1|yes|enabled|allowed|eligible|on)[\"']?\b"
)
_TEXT_VALUE_EQUALS_ASSIGNMENT_RE = re.compile(
    r"(?is)\b([a-z][^\n\r=]{0,96})=\s*[\"']?[A-Za-z0-9._/@+=-]{6,}[\"']?"
)
_TEXT_VALUE_LABEL_RE = re.compile(
    r"(?is)(?=\b([a-z][^\n\r=]{0,96}?)(?:\s*[:?&]\s*|\s+)"
    r"[\"']?[A-Za-z0-9._/@+=-]{6,}[\"']?"
    r")"
)
_TEXT_TRUTHY_EQUALS_ASSIGNMENT_RE = re.compile(
    r"(?is)\b([a-z][^\n\r=]{0,96})=\s*"
    r"[\"']?(true|1|yes|enabled|allowed|eligible|on)[\"']?\b"
)
_TEXT_TRUTHY_LABEL_RE = re.compile(
    r"(?is)(?=\b([a-z][^\n\r=]{0,96}?)(?:\s*:\s*|\s+)"
    r"[\"']?(true|1|yes|enabled|allowed|eligible|on)[\"']?\b"
    r")"
)
_TEXT_AUTHORITY_FIELD_NAMES = (
    _ACTIVATION_FIELD_NAMES
    | _MEMORY_AUTHORITY_FIELD_NAMES
    | _WORKSPACE_AUTHORITY_FIELD_NAMES
    | _EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES
    | _SCHEDULE_AUTHORITY_FIELD_NAMES
    | _LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES
    | _WRITE_APPLY_FIELD_NAMES
)


class _AuditEventsModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for recipe builder audit contracts")

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
        normalized = _to_validation_payload(data)
        _reject_unsafe_input(normalized)
        return normalized


class RecipeBuilderAuditScope(_AuditEventsModel):
    owner_id: str = Field(alias="ownerId")
    bot_id: str = Field(alias="botId")
    session_id: str = Field(alias="sessionId")

    @field_validator("owner_id", "bot_id", "session_id")
    @classmethod
    def _validate_scope_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "scope")


class RecipeBuilderAuditEventRef(_AuditEventsModel):
    event_id: str = Field(alias="eventId")
    event_type: RecipeBuilderAuditEventType = Field(alias="eventType")
    event_digest: str = Field(alias="eventDigest")

    @field_validator("event_id")
    @classmethod
    def _validate_event_id(cls, value: str) -> str:
        return _require_safe_ref(value, "eventId")

    @field_validator("event_digest")
    @classmethod
    def _validate_event_digest(cls, value: str) -> str:
        return _require_digest(value, "eventDigest")


class RecipeBuilderAuditEvent(_AuditEventsModel):
    schema_version: Literal["recipe_builder_audit_event.v1"] = Field(
        alias="schemaVersion"
    )
    scope: RecipeBuilderAuditScope
    event_id: str = Field(alias="eventId")
    event_type: RecipeBuilderAuditEventType = Field(alias="eventType")
    subject_ref: str = Field(alias="subjectRef")
    subject_digest: str = Field(alias="subjectDigest")
    policy_digest: str | None = Field(default=None, alias="policyDigest")
    artifact_digests: tuple[str, ...] = Field(default=(), alias="artifactDigests")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    redaction_status: RecipeBuilderAuditRedactionStatus = Field(alias="redactionStatus")
    summary: str
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    runtime_activation_eligible: StrictBool = Field(
        default=False, alias="runtimeActivationEligible"
    )
    connector_credentials_accessed: StrictBool = Field(
        default=False, alias="connectorCredentialsAccessed"
    )
    connector_credentials_restored: StrictBool = Field(
        default=False, alias="connectorCredentialsRestored"
    )
    schedules_restored: StrictBool = Field(default=False, alias="schedulesRestored")
    schedule_mutation_enabled: StrictBool = Field(
        default=False, alias="scheduleMutationEnabled"
    )
    memory_writes_enabled: StrictBool = Field(default=False, alias="memoryWritesEnabled")
    workspace_mutation_enabled: StrictBool = Field(
        default=False, alias="workspaceMutationEnabled"
    )
    external_delivery_enabled: StrictBool = Field(
        default=False, alias="externalDeliveryEnabled"
    )
    live_mode: StrictBool = Field(default=False, alias="liveMode")

    @field_validator("event_id", "subject_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "eventId/subjectRef")

    @field_validator("artifact_refs")
    @classmethod
    def _validate_artifact_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            _require_safe_ref(ref, "artifactRefs")
        return value

    @field_validator("subject_digest", "policy_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, "digest")

    @field_validator("artifact_digests")
    @classmethod
    def _validate_artifact_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            _require_digest(digest, "artifactDigests")
        return value

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_safe_summary(value, "summary")

    @model_validator(mode="after")
    def _require_default_off_authority(self) -> RecipeBuilderAuditEvent:
        _reject_true(self.activation_enabled, "activationEnabled")
        _reject_true(self.runtime_activation_eligible, "runtimeActivationEligible")
        _reject_true(
            self.connector_credentials_accessed,
            "connectorCredentialsAccessed",
        )
        _reject_true(
            self.connector_credentials_restored,
            "connectorCredentialsRestored",
        )
        _reject_true(self.schedules_restored, "schedulesRestored")
        _reject_true(self.schedule_mutation_enabled, "scheduleMutationEnabled")
        _reject_true(self.memory_writes_enabled, "memoryWritesEnabled")
        _reject_true(self.workspace_mutation_enabled, "workspaceMutationEnabled")
        _reject_true(self.external_delivery_enabled, "externalDeliveryEnabled")
        _reject_true(self.live_mode, "liveMode")
        return self


class RecipeBuilderAuditBatch(_AuditEventsModel):
    schema_version: Literal["recipe_builder_audit_batch.v1"] = Field(
        alias="schemaVersion"
    )
    scope: RecipeBuilderAuditScope
    events: tuple[RecipeBuilderAuditEvent, ...] = Field(min_length=1)
    event_refs: tuple[RecipeBuilderAuditEventRef, ...] = Field(
        default=(), alias="eventRefs"
    )
    event_count: StrictInt | None = Field(default=None, ge=1, alias="eventCount")
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    runtime_activation_eligible: StrictBool = Field(
        default=False, alias="runtimeActivationEligible"
    )
    connector_credentials_accessed: StrictBool = Field(
        default=False, alias="connectorCredentialsAccessed"
    )
    connector_credentials_restored: StrictBool = Field(
        default=False, alias="connectorCredentialsRestored"
    )
    schedules_restored: StrictBool = Field(default=False, alias="schedulesRestored")
    schedule_mutation_enabled: StrictBool = Field(
        default=False, alias="scheduleMutationEnabled"
    )
    memory_writes_enabled: StrictBool = Field(default=False, alias="memoryWritesEnabled")
    workspace_mutation_enabled: StrictBool = Field(
        default=False, alias="workspaceMutationEnabled"
    )
    external_delivery_enabled: StrictBool = Field(
        default=False, alias="externalDeliveryEnabled"
    )
    live_mode: StrictBool = Field(default=False, alias="liveMode")

    @model_validator(mode="after")
    def _require_batch_consistency(self) -> RecipeBuilderAuditBatch:
        _reject_default_off_authority(self)
        if self.event_count is not None and self.event_count != len(self.events):
            raise ValueError("eventCount must match events")
        for event in self.events:
            if event.scope != self.scope:
                raise ValueError("batch scope must match every event scope")

        supplied_refs = {ref.event_id: ref for ref in self.event_refs}
        if len(supplied_refs) != len(self.event_refs):
            raise ValueError("eventRefs must not contain duplicate eventId values")
        events_by_id = {event.event_id: event for event in self.events}
        if len(events_by_id) != len(self.events):
            raise ValueError("events must not contain duplicate eventId values")
        for supplied_ref in self.event_refs:
            event = events_by_id.get(supplied_ref.event_id)
            if event is None:
                raise ValueError("eventRefs must reference included events")
            if supplied_ref.event_type != event.event_type:
                raise ValueError("eventRefs eventType must match included events")
            if supplied_ref.event_digest != _digest_event_model(event):
                raise ValueError("event digest mismatch for eventRefs")
        if self.event_refs and set(supplied_refs) != set(events_by_id):
            raise ValueError("eventRefs must cover every included event")
        return self


class RecipeBuilderAuditValidationResult(_AuditEventsModel):
    status: RecipeBuilderAuditValidationStatus = "valid"
    validation_mode: RecipeBuilderAuditValidationMode = Field(
        default="validate_only",
        alias="validationMode",
    )
    scope: RecipeBuilderAuditScope
    accepted_event_refs: tuple[RecipeBuilderAuditEventRef, ...] = Field(
        alias="acceptedEventRefs"
    )
    event_count: StrictInt = Field(ge=0, alias="eventCount")
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    runtime_activation_eligible: StrictBool = Field(
        default=False, alias="runtimeActivationEligible"
    )
    connector_credentials_accessed: StrictBool = Field(
        default=False, alias="connectorCredentialsAccessed"
    )
    connector_credentials_restored: StrictBool = Field(
        default=False, alias="connectorCredentialsRestored"
    )
    schedules_restored: StrictBool = Field(default=False, alias="schedulesRestored")
    schedule_mutation_enabled: StrictBool = Field(
        default=False, alias="scheduleMutationEnabled"
    )
    memory_writes_enabled: StrictBool = Field(default=False, alias="memoryWritesEnabled")
    workspace_mutation_enabled: StrictBool = Field(
        default=False, alias="workspaceMutationEnabled"
    )
    external_delivery_enabled: StrictBool = Field(
        default=False, alias="externalDeliveryEnabled"
    )
    live_mode: StrictBool = Field(default=False, alias="liveMode")

    @model_validator(mode="after")
    def _require_result_default_off(self) -> RecipeBuilderAuditValidationResult:
        _reject_default_off_authority(self)
        return self


def digest_recipe_builder_audit_event(value: object) -> str:
    event = RecipeBuilderAuditEvent.model_validate(_to_validation_payload(value))
    return _digest_event_model(event)


def digest_recipe_builder_audit_batch(value: object) -> str:
    batch = RecipeBuilderAuditBatch.model_validate(_to_validation_payload(value))
    return _digest_json(batch.model_dump(by_alias=True))


def validate_recipe_builder_audit_batch(
    value: object,
) -> RecipeBuilderAuditValidationResult:
    batch = RecipeBuilderAuditBatch.model_validate(_to_validation_payload(value))
    refs = batch.event_refs or tuple(
        RecipeBuilderAuditEventRef(
            eventId=event.event_id,
            eventType=event.event_type,
            eventDigest=_digest_event_model(event),
        )
        for event in batch.events
    )
    return RecipeBuilderAuditValidationResult(
        scope=batch.scope,
        acceptedEventRefs=refs,
        eventCount=len(batch.events),
    )


def _reject_unsafe_input(value: object) -> None:
    if isinstance(value, BaseModel):
        _reject_unsafe_input(_to_validation_payload(value))
        return
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
                raise ValueError("runtime entrypoint fields are not accepted")
            if normalized in _LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES and nested is not False:
                if normalized in {
                    "connectorcredentialsaccessed",
                    "connectorcredentialsrestored",
                }:
                    raise ValueError(f"{key} cannot be true in audit event contracts")
                raise ValueError("connector credential authority is not accepted")
            if normalized in _SEPARATE_AGENT_IDENTITY_FIELD_NAMES:
                raise ValueError("separate Builder Agent identity is not accepted")
            if normalized in _ACTIVATION_FIELD_NAMES and nested is not False:
                raise ValueError(f"{key} cannot be true in audit event contracts")
            if normalized in _MEMORY_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError(f"{key} cannot be true in audit event contracts")
            if normalized in _WORKSPACE_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError(f"{key} cannot be true in audit event contracts")
            if normalized in _EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError(f"{key} cannot be true in audit event contracts")
            if normalized in _SCHEDULE_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError(f"{key} cannot be true in audit event contracts")
            if normalized == "livemode" and nested is not False:
                raise ValueError(f"{key} cannot be true in audit event contracts")
            if normalized in _WRITE_APPLY_FIELD_NAMES and nested is not False:
                raise ValueError("audit event validation is validate-only")
            _reject_unsafe_input(nested)
        return
    if isinstance(value, str):
        _reject_unsafe_string(value, "value")
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_unsafe_input(nested)


def _reject_unsafe_string(value: str, field_name: str) -> None:
    for candidate in _decoded_candidates(value):
        if not candidate.strip():
            continue
        if _ENCODED_CONTROL_TEXT_RE.search(candidate) or _has_non_whitespace_control(
            candidate
        ):
            raise ValueError(f"control characters are not accepted in {field_name}")
        lowered = candidate.lower()
        if _URI_USERINFO_RE.match(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _SIGNED_QUERY_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _SECRET_TEXT_RE.search(candidate) or _has_secret_assignment(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _RAW_MODEL_TEXT_RE.search(candidate):
            raise ValueError(f"raw model data is not accepted in {field_name}")
        if _is_private_scheme(lowered):
            raise ValueError(f"private URI schemes are not accepted in {field_name}")
        if _PRIVATE_PATH_TEXT_RE.search(candidate):
            raise ValueError(f"private paths are not accepted in {field_name}")
        if _has_raw_payload_label(candidate):
            raise ValueError(f"raw payload labels are not accepted in {field_name}")
        if _has_authority_assignment(candidate):
            raise ValueError(f"authoring authority is not accepted in {field_name}")
        if _SOURCE_CODE_TEXT_RE.search(candidate):
            raise ValueError(f"raw source code is not accepted in {field_name}")
        if _SEPARATE_BUILDER_AGENT_TEXT_RE.search(candidate):
            raise ValueError(f"separate Builder Agent identity is not accepted in {field_name}")
        _reject_authority_text(candidate, field_name)


def _is_private_scheme(lowered: str) -> bool:
    if (match := _URI_SCHEME_RE.match(lowered)) and match.group(1) in _PRIVATE_URI_SCHEMES:
        return True
    return any(
        match.group(1).lower() in _PRIVATE_URI_SCHEMES
        for match in _URI_SCHEME_ANYWHERE_RE.finditer(lowered)
    )


def _require_safe_ref(value: str, field_name: str) -> str:
    for candidate in _decoded_candidates(value):
        if not candidate.strip():
            raise ValueError(f"{field_name} must be non-empty")
        if "\\" in candidate:
            raise ValueError(f"{field_name} must not contain backslashes")
        if _URI_SCHEME_RE.match(candidate):
            raise ValueError(f"{field_name} must be a public ref, not a URI")
        if candidate.startswith(("/", "~")):
            raise ValueError(f"{field_name} must be a public ref")
        if "/" in candidate or candidate in {".", ".."} or ".." in candidate.split("."):
            raise ValueError(f"{field_name} must not contain private paths")
        if any(char.isspace() for char in candidate):
            raise ValueError(f"{field_name} must not contain whitespace")
        _reject_unsafe_string(candidate, field_name)
    return value


def _require_safe_summary(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_string(value, field_name)
    return value


def _reject_authority_text(value: str, field_name: str) -> None:
    if (
        _AFFIRMATIVE_AUTHORITY_TEXT_RE.search(value)
        or _AFFIRMATIVE_AUTHORITY_ACTION_RE.search(value)
        or _NOUN_FIRST_AUTHORITY_TEXT_RE.search(value)
    ):
        raise ValueError(f"authoring authority is not accepted in {field_name}")


def _has_raw_payload_label(value: str) -> bool:
    return any(
        _has_normalized_label(match.group(1), _RAW_PAYLOAD_LABEL_FIELD_NAMES)
        for match in _RAW_PAYLOAD_LABEL_RE.finditer(value)
    )


def _has_authority_assignment(value: str) -> bool:
    for pattern in (
        _TEXT_FIELD_ASSIGNMENT_RE,
        _TEXT_TRUTHY_EQUALS_ASSIGNMENT_RE,
        _TEXT_TRUTHY_LABEL_RE,
    ):
        for match in pattern.finditer(value):
            if _has_normalized_label(match.group(1), _TEXT_AUTHORITY_FIELD_NAMES):
                return True
    return False


def _has_secret_assignment(value: str) -> bool:
    return any(
        _has_normalized_label(match.group(1), _RAW_CREDENTIAL_FIELD_NAMES)
        for pattern in (_TEXT_VALUE_EQUALS_ASSIGNMENT_RE, _TEXT_VALUE_LABEL_RE)
        for match in pattern.finditer(value)
    )


def _has_normalized_label(label: str, field_names: set[str]) -> bool:
    return any(
        candidate in field_names for candidate in _normalized_label_suffixes(label)
    )


def _normalized_label_suffixes(label: str) -> tuple[str, ...]:
    parts = [part for part in re.split(r"\s+", label.strip()) if part]
    token_parts = re.findall(r"[A-Za-z0-9]+", label)
    candidates: list[str] = []
    for start_index in range(max(0, len(parts) - 6), len(parts)):
        normalized = _normalize_field_name(" ".join(parts[start_index:]))
        if normalized:
            candidates.append(normalized)
    for start_index in range(max(0, len(token_parts) - 8), len(token_parts)):
        normalized = "".join(part.lower() for part in token_parts[start_index:])
        if normalized:
            candidates.append(normalized)
    candidates.extend(part.lower() for part in token_parts)
    return tuple(dict.fromkeys(candidates))


def _reject_true(value: bool, alias: str) -> None:
    if value:
        raise ValueError(f"{alias} cannot be true in audit event contracts")


def _reject_default_off_authority(value: object) -> None:
    _reject_true(value.activation_enabled, "activationEnabled")
    _reject_true(value.runtime_activation_eligible, "runtimeActivationEligible")
    _reject_true(value.connector_credentials_accessed, "connectorCredentialsAccessed")
    _reject_true(value.connector_credentials_restored, "connectorCredentialsRestored")
    _reject_true(value.schedules_restored, "schedulesRestored")
    _reject_true(value.schedule_mutation_enabled, "scheduleMutationEnabled")
    _reject_true(value.memory_writes_enabled, "memoryWritesEnabled")
    _reject_true(value.workspace_mutation_enabled, "workspaceMutationEnabled")
    _reject_true(value.external_delivery_enabled, "externalDeliveryEnabled")
    _reject_true(value.live_mode, "liveMode")


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _digest_event_model(event: RecipeBuilderAuditEvent) -> str:
    return _digest_json(event.model_dump(by_alias=True))


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_DIGEST_PREFIX}{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _to_validation_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        payload: dict[str, object] = {}
        model_fields = type(value).model_fields
        field_names = set(model_fields)
        reserved_keys = set(field_names)
        reserved_keys.update(field.alias for field in model_fields.values() if field.alias)
        for name, field in model_fields.items():
            if not hasattr(value, name):
                continue
            alias = field.alias or name
            payload[alias] = _to_validation_payload(getattr(value, name))
        for key, nested in getattr(value, "__dict__", {}).items():
            if key not in field_names:
                _add_hidden_payload(
                    payload,
                    reserved_keys,
                    "__dict__",
                    key,
                    nested,
                )
        extra = getattr(value, "__pydantic_extra__", None)
        if isinstance(extra, Mapping):
            for key, nested in extra.items():
                _add_hidden_payload(
                    payload,
                    reserved_keys,
                    "__pydantic_extra__",
                    key,
                    nested,
                )
        elif extra is not None:
            payload["__pydantic_extra__"] = _to_validation_payload(extra)
        private = getattr(value, "__pydantic_private__", None)
        if isinstance(private, Mapping):
            for key, nested in private.items():
                _add_hidden_payload(
                    payload,
                    reserved_keys,
                    "__pydantic_private__",
                    key,
                    nested,
                )
        elif private is not None:
            payload["__pydantic_private__"] = _to_validation_payload(private)
        return payload
    if isinstance(value, Mapping):
        return {str(key): _to_validation_payload(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_validation_payload(nested) for nested in value]
    return value


def _add_hidden_payload(
    payload: dict[str, object],
    reserved_keys: set[str],
    source: str,
    key: object,
    nested: object,
) -> None:
    raw_key = str(key)
    payload_key = raw_key
    if raw_key in reserved_keys or raw_key in payload:
        payload_key = f"{source}.{raw_key}"
    payload[payload_key] = _to_validation_payload(nested)


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _decoded_candidates(value: str) -> tuple[str, ...]:
    if len(value) > 2048:
        raise ValueError("encoded values are too large to validate")
    seen: dict[str, None] = {}
    queue = [value]
    for _ in range(128):
        if not queue:
            return tuple(seen)
        candidate = queue.pop(0)
        if candidate in seen:
            continue
        if len(candidate) > 2048:
            raise ValueError("encoded values are too large to validate")
        seen[candidate] = None
        expanded = list(_percent_html_decoded_candidates(candidate))
        expanded.extend(_canonical_text_candidates(candidate))
        expanded.extend(_base64_decoded_candidates(candidate))
        for decoded in expanded:
            if decoded not in seen:
                queue.append(decoded)
    raise ValueError("encoded values must decode to a stable value")


def _percent_html_decoded_candidates(value: str) -> tuple[str, ...]:
    candidates = [value]
    decoded = value
    for _ in range(20):
        next_decoded = unquote(unescape(decoded))
        if next_decoded == decoded:
            break
        decoded = next_decoded
        candidates.append(decoded)
    else:
        raise ValueError("percent-encoded values must decode to a stable value")
    return tuple(dict.fromkeys(candidates))


def _canonical_text_candidates(value: str) -> tuple[str, ...]:
    candidates: list[str] = []
    normalized = unicode_normalize("NFKC", value)
    if normalized != value:
        candidates.append(normalized)
    without_format_chars = "".join(
        char for char in normalized if unicode_category(char) != "Cf"
    )
    if without_format_chars != normalized:
        candidates.append(without_format_chars)
    return tuple(dict.fromkeys(candidates))


def _base64_decoded_candidates(value: str) -> tuple[str, ...]:
    decoded: list[str] = []
    decoded.extend(_split_base64_decoded_candidates(value))
    for match in _BASE64_TOKEN_RE.finditer(value):
        token = match.group(1)
        if text := _base64_decoded_text(token):
            decoded.append(text)
    for match in _BASE64_SHORT_URLSAFE_TOKEN_RE.finditer(value):
        token = match.group(1)
        if text := _canonical_short_base64url_decoded_text(token):
            if _is_unsafe_decoded_text(text):
                decoded.append(text)
    return tuple(dict.fromkeys(decoded))


def _split_base64_decoded_candidates(value: str) -> tuple[str, ...]:
    decoded: list[str] = []
    for match in _BASE64_SPLIT_RUN_RE.finditer(value):
        pieces = match.group(1).split()
        for start_index in range(len(pieces)):
            token_parts: list[str] = []
            for end_index in range(start_index, len(pieces)):
                token_parts.append(pieces[end_index])
                if end_index == start_index:
                    continue
                token = "".join(token_parts)
                if len(token) < 4:
                    continue
                if len(token) > 512:
                    break
                if text := _canonical_base64_decoded_text(token):
                    if _is_unsafe_decoded_text(text) or _is_encoded_text_candidate(text):
                        decoded.append(text)
    return tuple(dict.fromkeys(decoded))


def _base64_decoded_text(token: str) -> str | None:
    result = _base64_decoded_raw_text(token)
    if result is None:
        return None
    _, text = result
    return text


def _canonical_short_base64url_decoded_text(token: str) -> str | None:
    return _canonical_base64_decoded_text(token)


def _canonical_base64_decoded_text(token: str) -> str | None:
    result = _base64_decoded_raw_text(token)
    if result is None:
        return None
    raw, text = result
    stripped = token.rstrip("=")
    standard = base64.b64encode(raw).decode("ascii").rstrip("=")
    urlsafe = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if stripped not in {standard, urlsafe}:
        return None
    return text


def _base64_decoded_raw_text(token: str) -> tuple[bytes, str] | None:
    padded = token + ("=" * (-len(token) % 4))
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text and all(_is_decoded_text_char(char) for char in text):
        return raw, text
    return None


def _is_decoded_text_char(char: str) -> bool:
    return char.isprintable() or char.isspace() or unicode_category(char)[0] == "C"


def _has_non_whitespace_control(value: str) -> bool:
    return any(
        unicode_category(char)[0] == "C"
        and unicode_category(char) != "Cf"
        and not char.isspace()
        for char in value
    )


def _is_encoded_text_candidate(value: str) -> bool:
    return bool(
        _BASE64_TOKEN_RE.search(value)
        or _BASE64_SHORT_URLSAFE_TOKEN_RE.search(value)
        or _BASE64_SPLIT_RUN_RE.search(value)
    )


def _is_unsafe_decoded_text(value: str) -> bool:
    for candidate in (value, *_canonical_text_candidates(value)):
        lowered = candidate.lower()
        if (
            _has_non_whitespace_control(candidate)
            or _ENCODED_CONTROL_TEXT_RE.search(candidate)
            or _URI_USERINFO_RE.match(candidate)
            or _SIGNED_QUERY_RE.search(candidate)
            or _SECRET_TEXT_RE.search(candidate)
            or _has_secret_assignment(candidate)
            or _RAW_MODEL_TEXT_RE.search(candidate)
            or _has_raw_payload_label(candidate)
            or _has_authority_assignment(candidate)
            or _SOURCE_CODE_TEXT_RE.search(candidate)
            or _SEPARATE_BUILDER_AGENT_TEXT_RE.search(candidate)
            or _AFFIRMATIVE_AUTHORITY_TEXT_RE.search(candidate)
            or _AFFIRMATIVE_AUTHORITY_ACTION_RE.search(candidate)
            or _NOUN_FIRST_AUTHORITY_TEXT_RE.search(candidate)
            or _is_private_scheme(lowered)
            or _PRIVATE_PATH_TEXT_RE.search(candidate)
        ):
            return True
    return False


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


__all__ = [
    "RecipeBuilderAuditBatch",
    "RecipeBuilderAuditEvent",
    "RecipeBuilderAuditEventRef",
    "RecipeBuilderAuditEventType",
    "RecipeBuilderAuditRedactionStatus",
    "RecipeBuilderAuditScope",
    "RecipeBuilderAuditValidationResult",
    "digest_recipe_builder_audit_batch",
    "digest_recipe_builder_audit_event",
    "validate_recipe_builder_audit_batch",
]
