from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import Field, field_serializer, field_validator, model_serializer, model_validator

from .types import EvidenceMetadataModel, validate_evidence_type_name


CustomEvidenceExtractorSourceKind = Literal[
    "tool_result",
    "adk_event",
    "transcript",
    "artifact",
    "verifier",
    "plugin",
]

MAX_CUSTOM_EVIDENCE_EXTRACTORS = 20
MAX_CUSTOM_EVIDENCE_FIELDS = 25
MAX_CUSTOM_EVIDENCE_SUCCESS_CONDITIONS = 10
MAX_CUSTOM_EVIDENCE_ONE_OF_VALUES = 20

_EXTRACTOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_DOT_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
_RESERVED_PATH_SEGMENTS = frozenset({"__proto__", "prototype", "constructor"})


def _validate_metadata_identifier(value: str, field_name: str) -> str:
    if not value.strip() or not _EXTRACTOR_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a non-empty metadata identifier")
    return value


def _require_string(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    return value


def _require_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name)


def _validate_optional_string(value: str | None, field_name: str) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must be non-empty when provided")
    return value


def _validate_field_name(value: str) -> str:
    if not value.strip() or len(value) > 80 or not _FIELD_NAME_RE.fullmatch(value):
        raise ValueError("field mapping names must be non-empty snake_case metadata names")
    return value


def _validate_dot_path(value: str) -> str:
    if not value.strip():
        raise ValueError("path must be non-empty")
    if value != value.strip():
        raise ValueError("path must not contain leading or trailing whitespace")
    if len(value) > 200:
        raise ValueError("path must be at most 200 characters")

    segments = value.split(".")
    if any(not segment for segment in segments):
        raise ValueError("path must not contain empty segments")
    if any(segment in _RESERVED_PATH_SEGMENTS for segment in segments):
        raise ValueError("path must not contain reserved prototype segments")
    if any(_DOT_PATH_SEGMENT_RE.fullmatch(segment) is None for segment in segments):
        raise ValueError("path must use dot-separated metadata segments only")
    return value


def _freeze_json_like(value: object, field_name: str) -> object:
    if value is None:
        return None
    if type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} must contain only finite JSON-like numbers")
        return value
    if isinstance(value, bytes | bytearray | memoryview) or callable(value):
        raise ValueError(f"{field_name} must contain only declarative JSON-like values")
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _validate_json_mapping_key(key, field_name): _freeze_json_like(
                    nested,
                    field_name,
                )
                for key, nested in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_json_like(item, field_name) for item in value)
    raise ValueError(f"{field_name} must contain only declarative JSON-like values")


def _validate_json_mapping_key(key: object, field_name: str) -> str:
    if type(key) is not str:
        raise ValueError(f"{field_name} mapping keys must be strings")
    return key


def _thaw_json_like(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_like(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_like(item) for item in value]
    return value


def _schema_revalidation_data(model: EvidenceMetadataModel) -> dict[str, object]:
    return model.model_dump(by_alias=False, mode="python", warnings=False)


class CustomEvidenceExtractorSource(EvidenceMetadataModel):
    kind: CustomEvidenceExtractorSourceKind
    tool_name: str | None = Field(default=None, alias="toolName")
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    event_id: str | None = Field(default=None, alias="eventId")
    event_type: str | None = Field(default=None, alias="eventType")
    transcript_entry_id: str | None = Field(default=None, alias="transcriptEntryId")
    turn_id: str | None = Field(default=None, alias="turnId")
    artifact_id: str | None = Field(default=None, alias="artifactId")
    verifier_name: str | None = Field(default=None, alias="verifierName")
    plugin_id: str | None = Field(default=None, alias="pluginId")
    plugin_name: str | None = Field(default=None, alias="pluginName")

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, cls):
            obj = _schema_revalidation_data(obj)
        return super().model_validate(
            obj,
            strict=strict,
            extra="forbid",
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("kind", mode="before")
    @classmethod
    def _validate_kind_input(cls, value: object) -> object:
        return _require_string(value, "source kind")

    @field_validator(
        "tool_name",
        "tool_call_id",
        "event_id",
        "event_type",
        "transcript_entry_id",
        "turn_id",
        "artifact_id",
        "verifier_name",
        "plugin_id",
        "plugin_name",
        mode="before",
    )
    @classmethod
    def _validate_optional_metadata_input(cls, value: object) -> str | None:
        return _require_optional_string(value, "source metadata")

    @field_validator(
        "tool_name",
        "tool_call_id",
        "event_id",
        "event_type",
        "transcript_entry_id",
        "turn_id",
        "artifact_id",
        "verifier_name",
        "plugin_id",
        "plugin_name",
    )
    @classmethod
    def _reject_empty_optional_metadata(cls, value: str | None) -> str | None:
        return _validate_optional_string(value, "source metadata")


class CustomEvidenceFieldMapping(EvidenceMetadataModel):
    name: str
    path: str
    required: bool = False
    default: object | None = None

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, cls):
            obj = _schema_revalidation_data(obj)
        return super().model_validate(
            obj,
            strict=strict,
            extra="forbid",
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name_input(cls, value: object) -> str:
        return _require_string(value, "field mapping name")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _validate_field_name(value)

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path_input(cls, value: object) -> str:
        return _require_string(value, "path")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_dot_path(value)

    @field_validator("required", mode="before")
    @classmethod
    def _validate_required(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("required must be a boolean")
        return value

    @field_validator("default")
    @classmethod
    def _validate_default(cls, value: object | None) -> object | None:
        return _freeze_json_like(value, "default")

    @field_serializer("default")
    def _serialize_default(self, value: object | None) -> object | None:
        return _thaw_json_like(value)


class CustomEvidenceSuccessCondition(EvidenceMetadataModel):
    path: str
    equals: object | None = None
    one_of: tuple[object, ...] | None = Field(default=None, alias="oneOf")
    exists: bool | None = None

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, cls):
            obj = _schema_revalidation_data(obj)
        return super().model_validate(
            obj,
            strict=strict,
            extra="forbid",
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path_input(cls, value: object) -> str:
        return _require_string(value, "path")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_dot_path(value)

    @field_validator("equals")
    @classmethod
    def _validate_equals(cls, value: object | None) -> object | None:
        if value is None:
            return None
        return _freeze_json_like(value, "equals")

    @field_validator("one_of", mode="before")
    @classmethod
    def _validate_one_of_input(cls, value: object | None) -> object | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("oneOf must be a JSON-like list")
        if not value:
            raise ValueError("oneOf must be non-empty when provided")
        if len(value) > MAX_CUSTOM_EVIDENCE_ONE_OF_VALUES:
            raise ValueError("oneOf must contain at most 20 values")
        return tuple(_freeze_json_like(item, "oneOf") for item in value)

    @field_validator("exists", mode="before")
    @classmethod
    def _validate_exists(cls, value: object) -> object:
        if value is not None and not isinstance(value, bool):
            raise ValueError("exists must be a boolean")
        return value

    @model_validator(mode="after")
    def _require_exactly_one_condition(self) -> Self:
        condition_fields = ("equals", "one_of", "exists")
        provided = [
            field_name
            for field_name in condition_fields
            if field_name in self.model_fields_set and getattr(self, field_name) is not None
        ]
        if any(
            field_name in self.model_fields_set and getattr(self, field_name) is None
            for field_name in condition_fields
        ):
            raise ValueError("success condition values must be non-null when provided")
        if len(provided) != 1:
            raise ValueError("success conditions must declare exactly one matcher")
        return self

    @field_serializer("equals")
    def _serialize_equals(self, value: object | None) -> object | None:
        return _thaw_json_like(value)

    @field_serializer("one_of")
    def _serialize_one_of(self, value: tuple[object, ...] | None) -> list[object] | None:
        if value is None:
            return None
        return [_thaw_json_like(item) for item in value]

    @model_serializer(mode="wrap")
    def _serialize_without_unset_null_matchers(self, handler: Any) -> dict[str, object]:
        serialized = handler(self)
        return {key: value for key, value in serialized.items() if value is not None}


class CustomEvidenceExtractor(EvidenceMetadataModel):
    id: str
    emits_type: str = Field(alias="emitsType")
    source: CustomEvidenceExtractorSource
    fields: tuple[CustomEvidenceFieldMapping, ...]
    success_when: tuple[CustomEvidenceSuccessCondition, ...] = Field(
        default_factory=tuple,
        alias="successWhen",
        validate_default=False,
    )

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, cls):
            obj = _extractor_revalidation_data(obj)
        return super().model_validate(
            obj,
            strict=strict,
            extra="forbid",
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = _extractor_revalidation_data(self)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        fields = data.get("fields")
        if fields is None:
            return data

        normalized: list[object]
        if isinstance(fields, Mapping):
            normalized = []
            for field_name, field_config in fields.items():
                if type(field_name) is not str:
                    raise ValueError("mapping-form field names must be strings")
                if not isinstance(field_config, Mapping):
                    raise ValueError("field mappings must be declarative mapping metadata")
                if "name" in field_config and field_config["name"] != field_name:
                    raise ValueError("mapping-form field names must match their mapping keys")
                normalized.append({**field_config, "name": field_name})
        elif isinstance(fields, list):
            normalized = list(fields)
        else:
            raise ValueError("fields must be a mapping or JSON-like list of field mappings")

        return {**data, "fields": normalized}

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id_input(cls, value: object) -> str:
        return _require_string(value, "extractor id")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _validate_metadata_identifier(value, "extractor id")

    @field_validator("emits_type", mode="before")
    @classmethod
    def _validate_emits_type_input(cls, value: object) -> str:
        return _require_string(value, "emitsType")

    @field_validator("emits_type")
    @classmethod
    def _validate_emits_type(cls, value: str) -> str:
        validated = validate_evidence_type_name(value)
        if not validated.startswith("custom:"):
            raise ValueError("custom extractors must emit custom:* evidence types")
        return validated

    @field_validator("source")
    @classmethod
    def _revalidate_source(
        cls,
        value: CustomEvidenceExtractorSource,
    ) -> CustomEvidenceExtractorSource:
        return CustomEvidenceExtractorSource.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )

    @field_validator("fields")
    @classmethod
    def _validate_fields(
        cls,
        value: tuple[CustomEvidenceFieldMapping, ...],
    ) -> tuple[CustomEvidenceFieldMapping, ...]:
        if not value:
            raise ValueError("custom extractors must declare at least one field mapping")
        if len(value) > MAX_CUSTOM_EVIDENCE_FIELDS:
            raise ValueError("custom extractors may declare at most 25 field mappings")

        names: set[str] = set()
        normalized: list[CustomEvidenceFieldMapping] = []
        for field in value:
            revalidated = CustomEvidenceFieldMapping.model_validate(
                field.model_dump(by_alias=False, mode="python", warnings=False)
            )
            if revalidated.name in names:
                raise ValueError("field mapping names must be unique")
            names.add(revalidated.name)
            normalized.append(revalidated)
        return tuple(normalized)

    @field_validator("success_when", mode="before")
    @classmethod
    def _validate_success_when_input(cls, value: object) -> object:
        if isinstance(value, list):
            return value
        raise ValueError("successWhen must be a JSON-like list")

    @field_validator("success_when")
    @classmethod
    def _validate_success_when(
        cls,
        value: tuple[CustomEvidenceSuccessCondition, ...],
    ) -> tuple[CustomEvidenceSuccessCondition, ...]:
        if len(value) > MAX_CUSTOM_EVIDENCE_SUCCESS_CONDITIONS:
            raise ValueError("custom extractors may declare at most 10 success conditions")
        return tuple(
            CustomEvidenceSuccessCondition.model_validate(
                condition.model_dump(by_alias=False, mode="python", warnings=False)
            )
            for condition in value
        )

    @field_serializer("fields")
    def _serialize_fields(
        self,
        value: tuple[CustomEvidenceFieldMapping, ...],
    ) -> dict[str, dict[str, object]]:
        return {
            field.name: field.model_dump(
                by_alias=True,
                exclude={"name"},
                exclude_none=True,
                warnings=False,
            )
            for field in value
        }


def _extractor_revalidation_data(extractor: CustomEvidenceExtractor) -> dict[str, object]:
    return {
        "id": extractor.id,
        "emits_type": extractor.emits_type,
        "source": extractor.source,
        "fields": list(extractor.fields),
        "success_when": list(extractor.success_when),
    }


class CustomEvidenceExtractorConfig(EvidenceMetadataModel):
    custom_evidence_extractors: tuple[CustomEvidenceExtractor, ...] = Field(
        default_factory=tuple,
        alias="customEvidenceExtractors",
        validate_default=False,
    )

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, cls):
            obj = {
                "custom_evidence_extractors": list(obj.custom_evidence_extractors),
            }
        return super().model_validate(
            obj,
            strict=strict,
            extra="forbid",
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data: dict[str, object] = {
            "custom_evidence_extractors": list(self.custom_evidence_extractors),
        }
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @field_validator("custom_evidence_extractors", mode="before")
    @classmethod
    def _validate_extractors_input(cls, value: object) -> object:
        if isinstance(value, list):
            return value
        raise ValueError("customEvidenceExtractors must be a JSON-like list")

    @field_validator("custom_evidence_extractors")
    @classmethod
    def _validate_extractors(
        cls,
        value: tuple[CustomEvidenceExtractor, ...],
    ) -> tuple[CustomEvidenceExtractor, ...]:
        if len(value) > MAX_CUSTOM_EVIDENCE_EXTRACTORS:
            raise ValueError("custom evidence config may declare at most 20 extractors")

        ids: set[str] = set()
        normalized: list[CustomEvidenceExtractor] = []
        for extractor in value:
            revalidated = CustomEvidenceExtractor.model_validate(
                _extractor_revalidation_data(extractor)
            )
            if revalidated.id in ids:
                raise ValueError("custom evidence extractor ids must be unique")
            ids.add(revalidated.id)
            normalized.append(revalidated)
        return tuple(normalized)


__all__ = [
    "CustomEvidenceExtractor",
    "CustomEvidenceExtractorConfig",
    "CustomEvidenceExtractorSource",
    "CustomEvidenceExtractorSourceKind",
    "CustomEvidenceFieldMapping",
    "CustomEvidenceSuccessCondition",
    "MAX_CUSTOM_EVIDENCE_EXTRACTORS",
    "MAX_CUSTOM_EVIDENCE_FIELDS",
    "MAX_CUSTOM_EVIDENCE_ONE_OF_VALUES",
    "MAX_CUSTOM_EVIDENCE_SUCCESS_CONDITIONS",
]
