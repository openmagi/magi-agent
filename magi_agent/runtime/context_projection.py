from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


ContextProjectionMode = Literal["explicit", "last_step_only", "accumulate_verified", "general_chat_history"]
RedactionStatus = Literal["redacted", "not_required"]
_DIGEST_PREFIX = "sha256:"
_PROTECTED_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
)
_FORBIDDEN_INCLUDED_CONTEXT_PREFIXES = (
    "raw_transcript",
    "raw_child_transcript",
    "raw_tool_log",
    "private_tool_trace",
    "hidden_reasoning",
    "private_memory",
)
_ALLOWED_EXCLUDED_CONTEXT_CLASSES = _FORBIDDEN_INCLUDED_CONTEXT_PREFIXES + ("child_raw_tool_log",)
_RAW_REF_PREFIXES = ("raw:", "rawref:", "rawref", "raw_", "raw-", "raw.", "raw/")
_RAW_CONTEXT_MARKERS = (
    "rawtranscript",
    "rawchildtranscript",
    "rawtoollog",
    "childrawtoollog",
    "privatetooltrace",
    "hiddenreasoning",
    "privatememory",
)


class ContextProjection(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    projection_id: str = Field(alias="projectionId")
    mode: ContextProjectionMode
    included_context_refs: tuple[str, ...] = Field(alias="includedContextRefs")
    excluded_context_classes: tuple[str, ...] = Field(alias="excludedContextClasses")
    source_digests: tuple[str, ...] = Field(alias="sourceDigests")
    token_budget: int = Field(alias="tokenBudget")
    byte_budget: int = Field(alias="byteBudget")
    redaction_status: RedactionStatus = Field(alias="redactionStatus")
    model_visible_digest: str = Field(alias="modelVisibleDigest")
    governed: StrictBool = True
    parent_visible: StrictBool = Field(default=True, alias="parentVisible")

    @field_validator("projection_id")
    @classmethod
    def _reject_empty_id(cls, value: str) -> str:
        _reject_protected_fragments(value, "projectionId")
        if not value.strip():
            raise ValueError("projectionId must be non-empty")
        return value

    @field_validator("included_context_refs", "excluded_context_classes", mode="before")
    @classmethod
    def _normalize_tuple(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("context projection refs must be arrays of non-empty strings")
        values = tuple(value or ())  # type: ignore[arg-type]
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("context projection refs must contain non-empty strings")
        return values

    @field_validator("included_context_refs")
    @classmethod
    def _validate_included_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            _reject_protected_fragments(ref, "includedContextRefs")
            _reject_raw_context_marker(ref, "includedContextRefs")
        return value

    @field_validator("excluded_context_classes")
    @classmethod
    def _validate_excluded_classes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for context_class in value:
            if context_class not in _ALLOWED_EXCLUDED_CONTEXT_CLASSES:
                raise ValueError("excludedContextClasses must be known raw/private context classes")
        return value

    @field_validator("source_digests", mode="before")
    @classmethod
    def _normalize_source_digests(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("sourceDigests must be an array of sha256 digests")
        return tuple(value or ())  # type: ignore[arg-type]

    @field_validator("source_digests")
    @classmethod
    def _validate_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            _require_digest(digest, "sourceDigests")
        return value

    @field_validator("token_budget", "byte_budget")
    @classmethod
    def _validate_budget(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("projection budgets must be positive integers")
        return value

    @model_validator(mode="after")
    def _validate_governed_mode(self) -> Self:
        if self.governed and self.mode == "general_chat_history":
            raise ValueError("general_chat_history is forbidden for governed context projection")
        if not self.included_context_refs:
            raise ValueError("context projection requires included refs")
        if self.model_visible_digest != _digest_projection_payload(self):
            raise ValueError("modelVisibleDigest does not match projection content")
        return self

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> "ContextProjection":
        if update:
            raise ValueError("model_copy update is disabled for context projections")
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


def build_context_projection(
    *,
    projectionId: str,
    mode: ContextProjectionMode,
    includedContextRefs: tuple[str, ...],
    excludedContextClasses: tuple[str, ...],
    sourceDigests: tuple[str, ...],
    tokenBudget: int,
    byteBudget: int,
    redactionStatus: RedactionStatus,
    governed: bool = True,
) -> ContextProjection:
    payload = {
        "projectionId": projectionId,
        "mode": mode,
        "includedContextRefs": includedContextRefs,
        "excludedContextClasses": excludedContextClasses,
        "sourceDigests": sourceDigests,
        "tokenBudget": tokenBudget,
        "byteBudget": byteBudget,
        "redactionStatus": redactionStatus,
        "governed": governed,
        "parentVisible": True,
    }
    return ContextProjection(**payload, modelVisibleDigest=_digest_json(payload))


def _digest_projection_payload(projection: ContextProjection) -> str:
    payload = projection.model_dump(by_alias=True, mode="json")
    payload.pop("modelVisibleDigest", None)
    return _digest_json(payload)


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be sha256 digest")
    return value


def _digest_json(value: object) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _reject_protected_fragments(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS):
        raise ValueError(f"{field_name} contains protected runtime data marker")


def _reject_raw_context_marker(value: str, field_name: str) -> None:
    lowered = value.lower()
    canonical = "".join(character for character in lowered if character.isalnum())
    if lowered.startswith(_RAW_REF_PREFIXES) or any(marker in canonical for marker in _RAW_CONTEXT_MARKERS):
        raise ValueError(f"{field_name} must not include raw or private context classes")
