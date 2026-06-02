from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


StructuredOutputStatus = Literal["disabled", "partial", "valid", "repair_required", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test|structured)?[-_A-Za-z0-9]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|browser|child)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"reasoning[_ -]?trace|model[_ -]?internal|authorization|cookie|set-cookie",
    re.IGNORECASE,
)


class StructuredOutputConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class StructuredOutputAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    runner_invoked: Literal[False] = Field(default=False, alias="runnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    production_output_committed: Literal[False] = Field(
        default=False,
        alias="productionOutputCommitted",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "runner_invoked",
        "model_called",
        "production_output_committed",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class StructuredOutputRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    turn_id: str = Field(alias="turnId")
    schema_name: str = Field(alias="schemaName")
    output_schema: Mapping[str, object] = Field(alias="schema")
    raw_output: str = Field(alias="rawOutput")
    is_final: bool = Field(default=True, alias="isFinal")

    @field_validator("output_schema")
    @classmethod
    def _copy_schema(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return json.loads(json.dumps(value))


class StructuredOutputDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: StructuredOutputStatus
    request_id: str = Field(alias="requestId")
    turn_id: str = Field(alias="turnId")
    schema_name: str = Field(alias="schemaName")
    parsed_output: object | None = Field(default=None, alias="parsedOutput")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: StructuredOutputAuthorityFlags = Field(
        default_factory=StructuredOutputAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = StructuredOutputAuthorityFlags()
        return cls.model_validate(values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requestId": _safe_ref(self.request_id, "request"),
            "turnId": _safe_ref(self.turn_id, "turn"),
            "schemaName": _safe_text(self.schema_name)[:120],
            "parsedOutput": _sanitize_output(self.parsed_output),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class StructuredOutputBoundary:
    """Default-off structured output contract validator."""

    def __init__(self, config: StructuredOutputConfig) -> None:
        self.config = config

    def validate(self, request: StructuredOutputRequest) -> StructuredOutputDecision:
        diagnostics: dict[str, object] = {
            "enabled": self.config.enabled,
            "runnerAttached": False,
            "modelCalled": False,
            "routeAttached": False,
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                ("structured_output_boundary_disabled",),
                diagnostics,
            )
        try:
            parsed = json.loads(request.raw_output)
        except json.JSONDecodeError as exc:
            if not request.is_final:
                return _decision(
                    request,
                    "partial",
                    ("partial_structured_output_pending",),
                    {**diagnostics, "parseOffset": exc.pos},
                )
            return _decision(
                request,
                "repair_required",
                ("malformed_structured_output",),
                {**diagnostics, "parseOffset": exc.pos},
            )
        if _contains_private_payload(parsed):
            return _decision(
                request,
                "blocked",
                ("private_structured_output_blocked",),
                diagnostics,
            )
        errors = _validate_schema(parsed, request.output_schema)
        if errors:
            return _decision(
                request,
                "blocked",
                ("structured_output_schema_mismatch",),
                {**diagnostics, "schemaErrorCount": len(errors), "schemaErrors": ",".join(errors[:3])},
            )
        return _decision(
            request,
            "valid",
            ("structured_output_schema_valid",),
            diagnostics,
            parsed_output=parsed,
        )


def _decision(
    request: StructuredOutputRequest,
    status: StructuredOutputStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    parsed_output: object | None = None,
) -> StructuredOutputDecision:
    return StructuredOutputDecision(
        status=status,
        requestId=request.request_id,
        turnId=request.turn_id,
        schemaName=request.schema_name,
        parsedOutput=parsed_output,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=StructuredOutputAuthorityFlags(),
    )


def _validate_schema(value: object, schema: Mapping[str, object], path: str = "$") -> list[str]:
    expected_type = schema.get("type")
    errors: list[str] = []
    if isinstance(expected_type, str) and not _matches_type(value, expected_type):
        return [f"{path}:type"]
    enum_values = schema.get("enum")
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, str):
        if value not in enum_values:
            errors.append(f"{path}:enum")
    if expected_type == "object" and isinstance(value, Mapping):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(required, str):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key}:required")
        if schema.get("additionalProperties") is False:
            allowed = {str(key) for key in properties.keys()}
            for key in value.keys():
                if str(key) not in allowed:
                    errors.append(f"{path}.{key}:additional")
        for key, nested_schema in properties.items():
            if key in value and isinstance(nested_schema, Mapping):
                errors.extend(_validate_schema(value[key], nested_schema, f"{path}.{key}"))
    if expected_type == "array" and isinstance(value, Sequence) and not isinstance(value, str):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, item_schema, f"{path}[{index}]"))
    return errors


def _matches_type(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _contains_private_payload(value: object) -> bool:
    if isinstance(value, str):
        return bool(_RAW_PRIVATE_LINE_RE.search(value) or _PRIVATE_PATH_RE.search(value) or _SECRET_TEXT_RE.search(value))
    if isinstance(value, Mapping):
        return any(_contains_private_payload(key) or _contains_private_payload(nested) for key, nested in value.items())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_private_payload(nested) for nested in value)
    return False


def _sanitize_output(value: object) -> object:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_output(nested)
            for key, nested in value.items()
            if not _contains_private_payload(key) and _sanitize_output(nested) not in ("", None)
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize_output(nested) for nested in value if _sanitize_output(nested) not in ("", None)]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return None


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in ("raw", "secret", "token", "path", "hidden")):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[str(key)] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_ref(value: str, prefix: str) -> str:
    clean = _safe_text(value)
    if clean and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{1,160}", clean):
        return clean
    return f"{prefix}:redacted"


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


__all__ = [
    "StructuredOutputAuthorityFlags",
    "StructuredOutputBoundary",
    "StructuredOutputConfig",
    "StructuredOutputDecision",
    "StructuredOutputRequest",
]
