from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)


EvidenceStatus = Literal["ok", "failed", "unknown"]
EvidenceSourceKind = Literal[
    "tool_trace",
    "adk_event",
    "transcript",
    "artifact",
    "execution_contract",
    "verifier",
    "custom_extractor",
    "external_ack",
]
EvidenceAfter = Literal["last_code_mutation", "contract_start"]
EvidenceTrigger = Literal["afterToolUse", "beforeCommit"]
EvidenceOnMissing = Literal["audit", "block_final_answer"]
EvidenceFailureCode = Literal[
    "EVIDENCE_CONTRACT_MISSING",
    "EVIDENCE_CONTRACT_STALE",
    "EVIDENCE_CONTRACT_FIELD_MISMATCH",
    "EVIDENCE_CONTRACT_COMMAND_MISMATCH",
    "EVIDENCE_CONTRACT_INVALID_CONFIG",
]
EvidenceEnforcement = Literal["off", "audit", "block_final_answer"]
EvidenceAgentRole = Literal["general", "coding", "research"]
EvidenceRunOn = Literal["main", "child"]
EvidenceVerdictState = Literal["audit", "pass", "missing", "failed", "block_ready"]

_BUILTIN_TYPE_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_CUSTOM_TYPE_RE = re.compile(r"^custom:[A-Z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$")
_CONTRACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_MAX_DECLARATIVE_PATTERN_LENGTH = 300
_UNSAFE_REGEX_TOKENS = ("(?=", "(?!", "(?<=", "(?<!", "(?P=", "(?(")
_ALL_INPUT_CHARACTER_CLASSES = (
    r"[\s\S]",
    r"[\S\s]",
    r"[\d\D]",
    r"[\D\d]",
    r"[\w\W]",
    r"[\W\w]",
)
_GROUPED_ALL_INPUT_EQUIVALENT_RE = re.compile(
    r"\((?:\?:)?(?:\.|\[(?:\\s\\S|\\S\\s|\\d\\D|\\D\\d|\\w\\W|\\W\\w)\])\)[*+]"
)
BUILTIN_EVIDENCE_TYPES: tuple[str, ...] = (
    "GitDiff",
    "TestRun",
    "CodeDiagnostics",
    "CommitCheckpoint",
    "FileDeliver",
    "ArtifactVerify",
    "DeterministicEvidenceVerifier",
    "WebSearch",
    "KnowledgeSearch",
    "SourceInspection",
    "PlanVerifier",
    "Calculation",
    "DateRange",
    "Clock",
    "TelegramDeliveryAck",
    "PromptTransform",
    "EditMatch",
    "DocumentCoverage",
)
_EVIDENCE_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)


class EvidenceMetadataModel(BaseModel):
    model_config = _EVIDENCE_MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


_EvidenceModelT = TypeVar("_EvidenceModelT", bound=EvidenceMetadataModel)


def _revalidate_nested_model(
    value: _EvidenceModelT,
    model_type: type[_EvidenceModelT],
) -> _EvidenceModelT:
    if isinstance(value, model_type):
        return model_type.model_validate(
            value.model_dump(
                by_alias=False,
                mode="python",
                warnings=False,
            )
        )
    return value


def validate_evidence_type_name(value: str) -> str:
    if not value.strip():
        raise ValueError("evidence type must be non-empty")
    if value.startswith("custom:"):
        if len(value) > 80 or not _CUSTOM_TYPE_RE.fullmatch(value):
            raise ValueError("custom evidence types must use custom:PascalCaseName metadata names")
        return value
    if ":" in value or not _BUILTIN_TYPE_RE.fullmatch(value):
        raise ValueError("built-in evidence types must use non-empty PascalCase metadata names")
    if value not in BUILTIN_EVIDENCE_TYPES:
        raise ValueError("non-custom evidence types must match the built-in evidence catalog exactly")
    return value


def _reject_empty_optional_string(value: str | None, field_name: str) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must be non-empty when provided")
    return value


def _validate_declarative_regex(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty when provided")
    if len(value) > _MAX_DECLARATIVE_PATTERN_LENGTH:
        raise ValueError(f"{field_name} must be at most 300 characters")
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"{field_name} must be a valid regex pattern") from exc
    _validate_restricted_regex(value, field_name)
    return value


def _validate_restricted_regex(value: str, field_name: str) -> None:
    if any(token in value for token in _UNSAFE_REGEX_TOKENS):
        raise ValueError(f"{field_name} must use restricted safe regex syntax")
    if _contains_unescaped_wildcard_dot(value):
        raise ValueError(f"{field_name} must not use wildcard dot patterns")
    if _contains_grouping_construct(value):
        raise ValueError(f"{field_name} must not use regex grouping constructs")
    if _contains_brace_quantifier(value):
        raise ValueError(f"{field_name} must not use brace quantifiers")
    if _contains_multiple_unescaped_quantifiers(value):
        raise ValueError(f"{field_name} must not use multiple regex quantifiers")
    if _contains_numeric_backreference(value):
        raise ValueError(f"{field_name} must not use regex backreferences")
    if _contains_unbounded_wildcard(value):
        raise ValueError(f"{field_name} must not use unbounded wildcard patterns")
    if _contains_all_input_equivalent(value):
        raise ValueError(f"{field_name} must not use all-input wildcard equivalent patterns")
    if _contains_nested_quantified_group(value):
        raise ValueError(f"{field_name} must not use nested quantified regex groups")
    if _contains_quantified_alternation(value):
        raise ValueError(f"{field_name} must not use quantified regex alternation groups")


def _contains_numeric_backreference(value: str) -> bool:
    escaped = False
    for char in value:
        if escaped:
            if char in "123456789":
                return True
            escaped = False
            continue
        escaped = char == "\\"
    return False


def _contains_unescaped_wildcard_dot(value: str) -> bool:
    escaped = False
    in_character_class = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if not in_character_class and char == ".":
            return True
    return False


def _contains_grouping_construct(value: str) -> bool:
    escaped = False
    in_character_class = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if not in_character_class and char in {"(", ")"}:
            return True
    return False


def _contains_brace_quantifier(value: str) -> bool:
    escaped = False
    in_character_class = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if not in_character_class and char in {"{", "}"}:
            return True
    return False


def _contains_multiple_unescaped_quantifiers(value: str) -> bool:
    quantifier_count = 0
    escaped = False
    in_character_class = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if not in_character_class and char in {"*", "+", "?"}:
            quantifier_count += 1
            if quantifier_count > 1:
                return True
    return False


def _contains_unbounded_wildcard(value: str) -> bool:
    escaped = False
    in_character_class = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if not in_character_class and char == "." and index + 1 < len(value):
            if value[index + 1] in {"*", "+"}:
                return True
    return False


def _contains_all_input_equivalent(value: str) -> bool:
    if _contains_grouped_all_input_equivalent(value):
        return True

    for character_class in _ALL_INPUT_CHARACTER_CLASSES:
        start = 0
        while True:
            index = value.find(character_class, start)
            if index == -1:
                break
            quantifier_index = index + len(character_class)
            if quantifier_index < len(value) and value[quantifier_index] in {"*", "+"}:
                return True
            start = index + 1

    return _contains_inline_dotall_wildcard(value)


def _contains_grouped_all_input_equivalent(value: str) -> bool:
    return any(
        _regex_token_is_unescaped_outside_class(value, match.start())
        for match in _GROUPED_ALL_INPUT_EQUIVALENT_RE.finditer(value)
    )


def _regex_token_is_unescaped_outside_class(value: str, index: int) -> bool:
    escaped = False
    in_character_class = False
    for position, char in enumerate(value):
        if position == index:
            return not escaped and not in_character_class
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
    return False


def _contains_inline_dotall_wildcard(value: str) -> bool:
    escaped = False
    in_character_class = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if in_character_class:
            continue
        if value.startswith("(?s:.)", index):
            quantifier_index = index + len("(?s:.)")
            if quantifier_index < len(value) and value[quantifier_index] in {"*", "+"}:
                return True
    return False


def _contains_nested_quantified_group(value: str) -> bool:
    group_quantifiers: list[bool] = []
    escaped = False
    in_character_class = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if in_character_class:
            continue
        if char == "(":
            group_quantifiers.append(False)
            continue
        if char == ")" and group_quantifiers:
            group_contains_quantifier = group_quantifiers.pop()
            group_is_quantified = _regex_quantifier_starts_at(value, index + 1)
            if group_contains_quantifier and group_is_quantified:
                return True
            if group_quantifiers and (group_contains_quantifier or group_is_quantified):
                group_quantifiers[-1] = True
            continue
        if char in {"*", "+", "?"} or char == "{":
            if char == "?" and index > 0 and value[index - 1] == "(":
                continue
            if group_quantifiers:
                group_quantifiers[-1] = True
    return False


def _contains_quantified_alternation(value: str) -> bool:
    group_alternation: list[bool] = []
    escaped = False
    in_character_class = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]":
            in_character_class = False
            continue
        if in_character_class:
            continue
        if char == "(":
            group_alternation.append(False)
            continue
        if char == ")" and group_alternation:
            group_contains_alternation = group_alternation.pop()
            group_is_quantified = _regex_quantifier_starts_at(value, index + 1)
            if group_contains_alternation and group_is_quantified:
                return True
            if group_alternation and group_contains_alternation:
                group_alternation[-1] = True
            continue
        if char == "|" and group_alternation:
            group_alternation[-1] = True
    return False


def _regex_quantifier_starts_at(value: str, index: int) -> bool:
    return index < len(value) and value[index] in {"*", "+", "?", "{"}


def _validate_observed_at(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("observedAt must be a finite int or float")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("observedAt must be a finite int or float")
    return value


def _validate_strict_bool(value: object, field_name: str) -> object:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _freeze_metadata_value(value: object, field_name: str = "metadata") -> object:
    if value is None:
        return value
    if type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} must contain only finite JSON-like floats")
        return value
    if isinstance(value, bytes | bytearray | memoryview) or callable(value):
        raise ValueError(f"{field_name} must contain only declarative JSON-like values")
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _validate_metadata_key(key, field_name): _freeze_metadata_value(
                    nested,
                    field_name,
                )
                for key, nested in value.items()
            }
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_metadata_value(item, field_name) for item in value)
    raise ValueError(f"{field_name} must contain only declarative JSON-like values")


def _validate_metadata_key(key: object, field_name: str) -> str:
    if type(key) is not str:
        raise ValueError(f"{field_name} mapping keys must be strings")
    return key


def _freeze_mapping(
    value: Mapping[str, object],
    field_name: str = "metadata",
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            _validate_metadata_key(key, field_name): _freeze_metadata_value(nested, field_name)
            for key, nested in value.items()
        }
    )


def _thaw_metadata_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_metadata_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_metadata_value(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw_metadata_value(item) for item in value]
    return value


def _serialize_mapping(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {key: _thaw_metadata_value(nested) for key, nested in value.items()}


class EvidenceFieldMatcher(EvidenceMetadataModel):
    equals: object | None = None
    one_of: tuple[object, ...] | None = Field(default=None, alias="oneOf")
    matches: str | None = None
    exists: bool | None = None

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(
            by_alias=False,
            mode="python",
            exclude_none=True,
            warnings=False,
        )
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @field_validator("equals")
    @classmethod
    def _validate_equals(cls, value: object | None) -> object | None:
        if value is None:
            return None
        return _freeze_metadata_value(value, "equals")

    @field_validator("one_of")
    @classmethod
    def _reject_empty_one_of(cls, value: tuple[object, ...] | None) -> tuple[object, ...] | None:
        if value is not None and not value:
            raise ValueError("oneOf must be non-empty when provided")
        if value is not None:
            return tuple(_freeze_metadata_value(item, "oneOf") for item in value)
        return value

    @field_validator("matches")
    @classmethod
    def _validate_declarative_pattern(cls, value: str | None) -> str | None:
        return _validate_declarative_regex(value, "matches")

    @field_validator("exists", mode="before")
    @classmethod
    def _validate_strict_exists(cls, value: object) -> object:
        if value is not None and not isinstance(value, bool):
            raise ValueError("exists must be a boolean")
        return value

    @model_validator(mode="after")
    def _require_at_least_one_matcher(self) -> Self:
        matcher_fields = {"equals", "one_of", "matches", "exists"}
        for field_name in self.model_fields_set & matcher_fields:
            if getattr(self, field_name) is None:
                raise ValueError("matcher values must be non-null when provided")
        if not any(
            field_name in self.model_fields_set and getattr(self, field_name) is not None
            for field_name in matcher_fields
        ):
            raise ValueError("field matcher must declare equals, oneOf, matches, or exists")
        return self

    @field_serializer("equals")
    def _serialize_equals(self, value: object | None) -> object | None:
        return _thaw_metadata_value(value)

    @field_serializer("one_of")
    def _serialize_one_of(self, value: tuple[object, ...] | None) -> tuple[object, ...] | None:
        if value is None:
            return None
        return tuple(_thaw_metadata_value(item) for item in value)

    @model_serializer(mode="wrap")
    def _serialize_without_unset_null_matchers(self, handler: Any) -> dict[str, object]:
        serialized = handler(self)
        return {key: value for key, value in serialized.items() if value is not None}


class EvidenceSource(EvidenceMetadataModel):
    kind: EvidenceSourceKind
    tool_name: str | None = Field(default=None, alias="toolName")
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    event_id: str | None = Field(default=None, alias="eventId")
    transcript_entry_id: str | None = Field(default=None, alias="transcriptEntryId")
    artifact_id: str | None = Field(default=None, alias="artifactId")
    contract_id: str | None = Field(default=None, alias="contractId")
    verifier_name: str | None = Field(default=None, alias="verifierName")
    extractor_id: str | None = Field(default=None, alias="extractorId")
    acknowledgement_id: str | None = Field(default=None, alias="acknowledgementId")
    channel: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator(
        "tool_name",
        "tool_call_id",
        "event_id",
        "transcript_entry_id",
        "artifact_id",
        "contract_id",
        "verifier_name",
        "extractor_id",
        "acknowledgement_id",
        "channel",
    )
    @classmethod
    def _reject_empty_optional_identifiers(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "source identifiers")

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value, "metadata")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}


class EvidenceRecord(EvidenceMetadataModel):
    type: str
    status: EvidenceStatus
    observed_at: int | float = Field(alias="observedAt")
    source: EvidenceSource
    fields: Mapping[str, object] = Field(default_factory=dict)
    preview: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        return validate_evidence_type_name(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _validate_observed_at(cls, value: object) -> int | float:
        return _validate_observed_at(value)

    @field_validator("preview")
    @classmethod
    def _reject_empty_preview(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "preview")

    @field_validator("source")
    @classmethod
    def _revalidate_source(cls, value: EvidenceSource) -> EvidenceSource:
        return _revalidate_nested_model(value, EvidenceSource)

    @field_validator("fields", "metadata")
    @classmethod
    def _freeze_mapping_fields(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value, "metadata")

    @field_serializer("fields", "metadata")
    def _serialize_mapping_fields(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}


class EvidenceRequirement(EvidenceMetadataModel):
    type: str
    after: EvidenceAfter | None = None
    command_pattern: str | None = Field(default=None, alias="commandPattern")
    exit_code: int | None = Field(default=None, alias="exitCode")
    fields: Mapping[str, EvidenceFieldMatcher] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        return validate_evidence_type_name(value)

    @field_validator("command_pattern")
    @classmethod
    def _validate_command_pattern(cls, value: str | None) -> str | None:
        return _validate_declarative_regex(value, "commandPattern")

    @field_validator("exit_code", mode="before")
    @classmethod
    def _validate_strict_exit_code(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("exitCode must be an integer")
        return value

    @field_validator("fields")
    @classmethod
    def _reject_empty_field_names(
        cls,
        value: Mapping[str, EvidenceFieldMatcher],
    ) -> Mapping[str, EvidenceFieldMatcher]:
        if any(not field_name.strip() for field_name in value):
            raise ValueError("evidence requirement field names must be non-empty")
        return MappingProxyType(
            {
                field_name: _revalidate_nested_model(matcher, EvidenceFieldMatcher)
                for field_name, matcher in value.items()
            }
        )

    @field_serializer("fields")
    def _serialize_fields(
        self,
        value: Mapping[str, EvidenceFieldMatcher],
    ) -> dict[str, EvidenceFieldMatcher]:
        return dict(value)


class EvidenceSpawnDepthRange(EvidenceMetadataModel):
    min_depth: int = Field(default=0, alias="minDepth")
    max_depth: int | None = Field(default=None, alias="maxDepth")

    @model_validator(mode="after")
    def _validate_depth_range(self) -> Self:
        if self.min_depth < 0:
            raise ValueError("minDepth must be non-negative")
        if self.max_depth is not None and self.max_depth < self.min_depth:
            raise ValueError("maxDepth must be greater than or equal to minDepth")
        return self


class EvidenceContractScopeMetadata(EvidenceMetadataModel):
    agent_roles: tuple[EvidenceAgentRole, ...] = Field(alias="agentRoles")
    run_on: tuple[EvidenceRunOn, ...] = Field(alias="runOn")
    spawn_depth: EvidenceSpawnDepthRange = Field(
        default_factory=EvidenceSpawnDepthRange,
        alias="spawnDepth",
    )
    enforcement: EvidenceEnforcement = "off"
    audit_before_block: bool = Field(default=True, alias="auditBeforeBlock")
    opt_out_allowed: bool = Field(default=True, alias="optOutAllowed")
    hard_safety: bool = Field(default=False, alias="hardSafety")
    failure_channel: Literal["evidence_contract"] = Field(
        default="evidence_contract",
        alias="failureChannel",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator(
        "audit_before_block",
        "opt_out_allowed",
        "hard_safety",
        "traffic_attached",
        "execution_attached",
        mode="before",
    )
    @classmethod
    def _validate_strict_booleans(cls, value: object, info: Any) -> object:
        return _validate_strict_bool(value, info.field_name)

    @field_validator("agent_roles", "run_on")
    @classmethod
    def _reject_empty_or_duplicate_scope_values(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("scope values must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("scope values must not contain duplicates")
        return value

    @field_validator("spawn_depth")
    @classmethod
    def _revalidate_spawn_depth(
        cls,
        value: EvidenceSpawnDepthRange,
    ) -> EvidenceSpawnDepthRange:
        return _revalidate_nested_model(value, EvidenceSpawnDepthRange)

    @model_validator(mode="after")
    def _validate_scope_policy(self) -> Self:
        if self.hard_safety and self.opt_out_allowed:
            raise ValueError("hard-safety evidence metadata cannot be opt-out allowed")
        if self.enforcement == "block_final_answer" and not self.audit_before_block:
            raise ValueError("block_final_answer evidence metadata requires audit-before-block")
        return self


class EvidenceContract(EvidenceMetadataModel):
    id: str
    description: str | None = None
    triggers: tuple[EvidenceTrigger, ...]
    when: Mapping[str, object] | None = None
    requirements: tuple[EvidenceRequirement, ...]
    on_missing: EvidenceOnMissing = Field(alias="onMissing")
    retry_message: str | None = Field(default=None, alias="retryMessage")
    scope: EvidenceContractScopeMetadata | None = None
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("traffic_attached", "execution_attached", mode="before")
    @classmethod
    def _validate_strict_attachment_flags(cls, value: object, info: Any) -> object:
        return _validate_strict_bool(value, info.field_name)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not value.strip() or not _CONTRACT_ID_RE.fullmatch(value):
            raise ValueError("evidence contract id must be non-empty metadata identifier")
        return value

    @field_validator("description", "retry_message")
    @classmethod
    def _reject_empty_optional_text(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "contract text")

    @field_validator("triggers", "requirements")
    @classmethod
    def _reject_empty_tuples(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if not value:
            raise ValueError("evidence contract triggers and requirements must be non-empty")
        return value

    @field_validator("when")
    @classmethod
    def _freeze_when(cls, value: Mapping[str, object] | None) -> Mapping[str, object] | None:
        if value is None:
            return None
        return _freeze_mapping(value, "when")

    @field_validator("requirements")
    @classmethod
    def _revalidate_requirements(
        cls,
        value: tuple[EvidenceRequirement, ...],
    ) -> tuple[EvidenceRequirement, ...]:
        return tuple(
            _revalidate_nested_model(requirement, EvidenceRequirement)
            for requirement in value
        )

    @field_validator("scope")
    @classmethod
    def _revalidate_scope(
        cls,
        value: EvidenceContractScopeMetadata | None,
    ) -> EvidenceContractScopeMetadata | None:
        if value is None:
            return None
        return _revalidate_nested_model(value, EvidenceContractScopeMetadata)

    @field_serializer("when")
    def _serialize_when(self, value: Mapping[str, object] | None) -> dict[str, object] | None:
        return _serialize_mapping(value)


class EvidenceContractFailure(EvidenceMetadataModel):
    code: EvidenceFailureCode
    contract_id: str = Field(alias="contractId")
    requirement_type: str | None = Field(default=None, alias="requirementType")
    message: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        if not value.strip() or not _CONTRACT_ID_RE.fullmatch(value):
            raise ValueError("contractId must be a non-empty metadata identifier")
        return value

    @field_validator("requirement_type")
    @classmethod
    def _validate_requirement_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_evidence_type_name(value)

    @field_validator("message")
    @classmethod
    def _reject_empty_message(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "failure message")

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value, "metadata")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}


class EvidenceContractVerdict(EvidenceMetadataModel):
    contract_id: str = Field(alias="contractId")
    ok: bool
    state: EvidenceVerdictState
    enforcement: Literal["audit", "block_final_answer"]
    missing_requirements: tuple[EvidenceRequirement, ...] = Field(alias="missingRequirements")
    matched_evidence: tuple[EvidenceRecord, ...] = Field(alias="matchedEvidence")
    failures: tuple[EvidenceContractFailure, ...]
    retry_message: str | None = Field(default=None, alias="retryMessage")
    requirement_coverage: tuple[str, ...] = Field(default=(), alias="requirementCoverage")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("ok", "traffic_attached", "execution_attached", mode="before")
    @classmethod
    def _validate_strict_booleans(cls, value: object, info: Any) -> object:
        return _validate_strict_bool(value, info.field_name)

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        if not value.strip() or not _CONTRACT_ID_RE.fullmatch(value):
            raise ValueError("contractId must be a non-empty metadata identifier")
        return value

    @field_validator("requirement_coverage")
    @classmethod
    def _validate_requirement_coverage(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        coverage = tuple(validate_evidence_type_name(item) for item in value)
        if len(set(coverage)) != len(coverage):
            raise ValueError("requirementCoverage must not contain duplicate types")
        return coverage

    @field_validator("retry_message")
    @classmethod
    def _reject_empty_retry_message(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "retryMessage")

    @field_validator("missing_requirements")
    @classmethod
    def _revalidate_missing_requirements(
        cls,
        value: tuple[EvidenceRequirement, ...],
    ) -> tuple[EvidenceRequirement, ...]:
        return tuple(
            _revalidate_nested_model(requirement, EvidenceRequirement)
            for requirement in value
        )

    @field_validator("matched_evidence")
    @classmethod
    def _revalidate_matched_evidence(
        cls,
        value: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        return tuple(_revalidate_nested_model(record, EvidenceRecord) for record in value)

    @field_validator("failures")
    @classmethod
    def _revalidate_failures(
        cls,
        value: tuple[EvidenceContractFailure, ...],
    ) -> tuple[EvidenceContractFailure, ...]:
        return tuple(
            _revalidate_nested_model(failure, EvidenceContractFailure)
            for failure in value
        )

    @model_serializer(mode="wrap")
    def _serialize_wire_verdict_without_attachment_flags(
        self,
        handler: Any,
    ) -> dict[str, object]:
        serialized = handler(self)
        for key in (
            "trafficAttached",
            "executionAttached",
            "traffic_attached",
            "execution_attached",
        ):
            serialized.pop(key, None)
        return serialized


__all__ = [
    "EvidenceAfter",
    "EvidenceAgentRole",
    "EvidenceContract",
    "EvidenceContractFailure",
    "EvidenceContractScopeMetadata",
    "EvidenceContractVerdict",
    "EvidenceEnforcement",
    "EvidenceFailureCode",
    "EvidenceFieldMatcher",
    "EvidenceOnMissing",
    "EvidenceRecord",
    "EvidenceRequirement",
    "EvidenceRunOn",
    "EvidenceSource",
    "EvidenceSourceKind",
    "EvidenceSpawnDepthRange",
    "EvidenceStatus",
    "EvidenceTrigger",
    "EvidenceVerdictState",
    "BUILTIN_EVIDENCE_TYPES",
    "EvidenceMetadataModel",
    "validate_evidence_type_name",
]
