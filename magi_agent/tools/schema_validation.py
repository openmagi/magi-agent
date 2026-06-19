from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from magi_agent.ops.safety import UNSAFE_TEXT_RE

from .manifest import ToolManifest


SchemaValidationStatus = Literal["valid", "invalid"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
# C-1: forked secret/private-text regex replaced by the union denylist kernel
# in ops/safety (strict superset; local placeholder/clip preserved below).
_PRIVATE_TEXT_RE = UNSAFE_TEXT_RE
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "credential",
    "secret",
    "token",
    "password",
    "privatekey",
    "apikey",
    "servicekey",
    "key",
    "path",
)


class ToolSchemaValidationDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: SchemaValidationStatus
    valid: bool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    @field_serializer("diagnostic_metadata")
    def _serialize_diagnostics(self, value: Mapping[str, object]) -> dict[str, object]:
        return _safe_metadata(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "valid": self.valid,
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
        }


def validate_tool_arguments(
    manifest: ToolManifest,
    arguments: Mapping[str, object],
) -> ToolSchemaValidationDecision:
    """Validate tool args against a manifest JSON-schema subset used by ADK FunctionTool."""

    schema = manifest.input_schema
    reasons: list[str] = []
    diagnostics: dict[str, object] = {
        "toolName": manifest.name,
        "schemaType": _schema_type_label(schema),
        "schemaDigest": _schema_digest(schema),
    }

    _validate_schema_value(
        schema,
        arguments,
        reasons=reasons,
        path=(),
        diagnostics=diagnostics,
    )

    reason_codes = tuple(dict.fromkeys(reasons))
    valid = not reason_codes
    return ToolSchemaValidationDecision(
        status="valid" if valid else "invalid",
        valid=valid,
        reasonCodes=reason_codes,
        diagnosticMetadata=diagnostics,
    )


def _validate_schema_value(
    schema: Mapping[str, object],
    value: object,
    *,
    reasons: list[str],
    path: tuple[str, ...],
    diagnostics: dict[str, object],
) -> None:
    if not schema:
        return

    if "allOf" in schema and isinstance(schema["allOf"], Sequence):
        for nested in schema["allOf"]:
            if isinstance(nested, Mapping):
                _validate_schema_value(
                    nested,
                    value,
                    reasons=reasons,
                    path=path,
                    diagnostics=diagnostics,
                )

    if "anyOf" in schema and isinstance(schema["anyOf"], Sequence):
        if not any(_matches_without_mutating(nested, value) for nested in schema["anyOf"]):
            reasons.append("schema_any_of_no_match")

    if "oneOf" in schema and isinstance(schema["oneOf"], Sequence):
        matches = sum(1 for nested in schema["oneOf"] if _matches_without_mutating(nested, value))
        if matches != 1:
            reasons.append("schema_one_of_no_match")

    expected = schema.get("type")
    if isinstance(expected, str) and not _type_matches(expected, value):
        reasons.append(f"schema_type_{expected}_expected")
        _add_path_diagnostic(diagnostics, "typeMismatchPaths", path)
        return
    if isinstance(expected, Sequence) and not isinstance(expected, str):
        expected_types = [item for item in expected if isinstance(item, str)]
        if expected_types and not any(_type_matches(item, value) for item in expected_types):
            reasons.append("schema_type_expected")
            _add_path_diagnostic(diagnostics, "typeMismatchPaths", path)
            return

    if "const" in schema and value != schema["const"]:
        reasons.append("schema_const_mismatch")
    if "enum" in schema and isinstance(schema["enum"], Sequence) and value not in schema["enum"]:
        reasons.append("schema_enum_mismatch")

    if isinstance(value, Mapping):
        _validate_object(schema, value, reasons=reasons, path=path, diagnostics=diagnostics)
    elif isinstance(value, list | tuple):
        _validate_array(schema, value, reasons=reasons, path=path, diagnostics=diagnostics)
    elif isinstance(value, str):
        _validate_string(schema, value, reasons=reasons, path=path, diagnostics=diagnostics)
    elif isinstance(value, int) and not isinstance(value, bool):
        _validate_number(schema, value, reasons=reasons, path=path, diagnostics=diagnostics)
    elif isinstance(value, float):
        _validate_number(schema, value, reasons=reasons, path=path, diagnostics=diagnostics)


def _validate_object(
    schema: Mapping[str, object],
    value: Mapping[object, object],
    *,
    reasons: list[str],
    path: tuple[str, ...],
    diagnostics: dict[str, object],
) -> None:
    required = schema.get("required")
    if isinstance(required, Sequence) and not isinstance(required, str):
        for key in required:
            if isinstance(key, str) and key not in value:
                reasons.append("schema_required_field_missing")
                _add_path_diagnostic(diagnostics, "missingRequiredPaths", (*path, key))

    properties = schema.get("properties")
    property_schemas = properties if isinstance(properties, Mapping) else {}
    if schema.get("additionalProperties") is False:
        for key in value:
            key_text = str(key)
            if key_text not in property_schemas:
                reasons.append("schema_additional_property_blocked")
                _add_path_diagnostic(diagnostics, "additionalPropertyPaths", (*path, key_text))

    for key, nested in property_schemas.items():
        if isinstance(key, str) and key in value and isinstance(nested, Mapping):
            _validate_schema_value(
                nested,
                value[key],
                reasons=reasons,
                path=(*path, key),
                diagnostics=diagnostics,
            )


def _validate_array(
    schema: Mapping[str, object],
    value: Sequence[object],
    *,
    reasons: list[str],
    path: tuple[str, ...],
    diagnostics: dict[str, object],
) -> None:
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if isinstance(min_items, int) and len(value) < min_items:
        reasons.append("schema_min_items_not_met")
        _add_path_diagnostic(diagnostics, "lengthViolationPaths", path)
    if isinstance(max_items, int) and len(value) > max_items:
        reasons.append("schema_max_items_exceeded")
        _add_path_diagnostic(diagnostics, "lengthViolationPaths", path)

    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            _validate_schema_value(
                items,
                item,
                reasons=reasons,
                path=(*path, str(index)),
                diagnostics=diagnostics,
            )


def _validate_string(
    schema: Mapping[str, object],
    value: str,
    *,
    reasons: list[str],
    path: tuple[str, ...],
    diagnostics: dict[str, object],
) -> None:
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if isinstance(min_length, int) and len(value) < min_length:
        reasons.append("schema_min_length_not_met")
        _add_path_diagnostic(diagnostics, "lengthViolationPaths", path)
    if isinstance(max_length, int) and len(value) > max_length:
        reasons.append("schema_max_length_exceeded")
        _add_path_diagnostic(diagnostics, "lengthViolationPaths", path)
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.search(pattern, value) is None:
        reasons.append("schema_pattern_mismatch")
        _add_path_diagnostic(diagnostics, "patternMismatchPaths", path)


def _validate_number(
    schema: Mapping[str, object],
    value: int | float,
    *,
    reasons: list[str],
    path: tuple[str, ...],
    diagnostics: dict[str, object],
) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int | float) and value < minimum:
        reasons.append("schema_minimum_not_met")
        _add_path_diagnostic(diagnostics, "rangeViolationPaths", path)
    if isinstance(maximum, int | float) and value > maximum:
        reasons.append("schema_maximum_exceeded")
        _add_path_diagnostic(diagnostics, "rangeViolationPaths", path)


def _matches_without_mutating(schema: object, value: object) -> bool:
    if not isinstance(schema, Mapping):
        return False
    reasons: list[str] = []
    _validate_schema_value(schema, value, reasons=reasons, path=(), diagnostics={})
    return not reasons


def _type_matches(expected: str, value: object) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list | tuple)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _schema_type_label(schema: Mapping[str, object]) -> str:
    value = schema.get("type")
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, str):
        return "|".join(str(item) for item in value)
    return "unspecified"


def _schema_digest(schema: Mapping[str, object]) -> str:
    text = repr(sorted((str(key), repr(value)) for key, value in schema.items()))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _add_path_diagnostic(
    diagnostics: dict[str, object],
    key: str,
    path: tuple[str, ...],
) -> None:
    paths = diagnostics.setdefault(key, [])
    if isinstance(paths, list):
        paths.append(_safe_path_ref(path))


def _safe_path_ref(path: tuple[str, ...]) -> str:
    if not path:
        return "$"
    return "arg:" + hashlib.sha1(".".join(path).encode("utf-8")).hexdigest()[:16]


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_sensitive_key(key_text) or _PRIVATE_TEXT_RE.search(key_text):
            continue
        clean_key = _safe_text(key_text)
        if not clean_key:
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[clean_key] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[clean_key] = value
        elif isinstance(value, Sequence) and not isinstance(value, str):
            safe[clean_key] = tuple(
                item
                for item in (_safe_text(str(nested)) for nested in value)
                if item
            )[:20]
    return safe


def _safe_text(value: str) -> str:
    return _PRIVATE_TEXT_RE.sub("[redacted-private]", value).strip()


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = ["ToolSchemaValidationDecision", "validate_tool_arguments"]
