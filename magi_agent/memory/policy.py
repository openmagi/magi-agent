from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from .contracts import RecallRequest


MAGI_MEMORY_PROJECTION_ENABLED_ENV: str = "MAGI_MEMORY_PROJECTION_ENABLED"


MemoryMode = Literal["normal", "read_only", "incognito"]
MemorySourceAuthority = Literal[
    "long_term_allowed",
    "long_term_disabled",
    "background_only",
    "memory_redact_authority",
    "child_isolated",
]

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


def _force_false_fields(
    model_cls: type[BaseModel],
    values: Mapping[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    payload = dict(values or {})
    for field_name in fields:
        field = model_cls.model_fields[field_name]
        payload.pop(field_name, None)
        payload[field.alias or field_name] = False
    return payload


class MemoryPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    memory_mode: MemoryMode = Field(default="normal", alias="memoryMode")
    source_authority: MemorySourceAuthority = Field(
        default="long_term_disabled",
        alias="sourceAuthority",
    )
    prompt_projection_enabled: Literal[False] = Field(
        default=False,
        alias="promptProjectionEnabled",
    )
    writes_enabled: Literal[False] = Field(default=False, alias="writesEnabled")

    @model_validator(mode="before")
    @classmethod
    def _force_false_only_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _force_false_fields(
            cls,
            value,
            ("prompt_projection_enabled", "writes_enabled"),
        )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(
            _force_false_fields(cls, values, ("prompt_projection_enabled", "writes_enabled"))
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        return type(self).model_validate(
            _force_false_fields(
                type(self),
                payload,
                ("prompt_projection_enabled", "writes_enabled"),
            )
        )

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude
        return self.model_copy(update=update, deep=deep)

    @field_serializer("prompt_projection_enabled", "writes_enabled")
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryPolicyDecision(BaseModel):
    model_config = _MODEL_CONFIG

    recall_allowed: bool = Field(alias="recallAllowed")
    write_allowed: bool = Field(alias="writeAllowed")
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    public_projection_allowed: bool = Field(alias="publicProjectionAllowed")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @model_validator(mode="before")
    @classmethod
    def _force_no_writes_or_prompt_projection(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _force_false_fields(
            cls,
            value,
            ("write_allowed", "prompt_projection_allowed"),
        )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(
            _force_false_fields(
                cls,
                values,
                ("write_allowed", "prompt_projection_allowed"),
            )
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        return type(self).model_validate(
            _force_false_fields(
                type(self),
                payload,
                ("write_allowed", "prompt_projection_allowed"),
            )
        )

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude
        return self.model_copy(update=update, deep=deep)

    @field_serializer("write_allowed", "prompt_projection_allowed")
    def _serialize_decision_false(self, _value: object) -> bool:
        return False


def evaluate_memory_policy(
    request: RecallRequest,
    policy: MemoryPolicy,
    *,
    write_intent: bool = False,
) -> MemoryPolicyDecision:
    _ = request
    reasons: list[str] = ["prompt_projection_disabled"]
    recall_allowed = True
    public_projection_allowed = True

    if policy.memory_mode == "incognito":
        recall_allowed = False
        public_projection_allowed = False
        reasons.append("incognito_blocks_recall")

    if policy.source_authority == "long_term_disabled":
        recall_allowed = False
        public_projection_allowed = False
        reasons.append("source_authority_disables_long_term_memory")
    elif policy.source_authority == "child_isolated":
        recall_allowed = False
        public_projection_allowed = False
        reasons.append("child_memory_scope_isolated")
    elif policy.source_authority == "memory_redact_authority":
        recall_allowed = False
        public_projection_allowed = False
        reasons.append("memory_redact_authority_supersedes_provider")
    elif policy.source_authority == "background_only":
        public_projection_allowed = False
        reasons.append("source_authority_background_only")

    write_allowed = False
    if write_intent:
        reasons.append("memory_writes_disabled")
        if policy.memory_mode == "read_only":
            reasons.append("read_only_blocks_writes")
        if policy.memory_mode == "incognito":
            reasons.append("incognito_blocks_writes")

    return MemoryPolicyDecision(
        recall_allowed=recall_allowed,
        write_allowed=write_allowed,
        prompt_projection_allowed=False,
        public_projection_allowed=public_projection_allowed,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


class MemoryProjectionGateDecision(BaseModel):
    """Result of the gated projection-tier check (D3).

    Unlike :class:`MemoryPolicyDecision`, this type allows
    ``prompt_projection_allowed=True`` — but only when the env gate is ON
    and the channel is not incognito.  The base
    :func:`evaluate_memory_policy` / :class:`MemoryPolicyDecision` remain
    pinned to ``False``; this decision type is *additive*.
    """

    model_config = _MODEL_CONFIG

    prompt_projection_allowed: bool = Field(alias="promptProjectionAllowed")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        return type(self).model_validate(payload)


def _projection_gate_open() -> bool:
    """Return True when ``MAGI_MEMORY_PROJECTION_ENABLED`` is set to a truthy value."""
    return os.environ.get(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def evaluate_memory_policy_with_gate(
    request: RecallRequest,
    policy: MemoryPolicy,
) -> MemoryProjectionGateDecision:
    """Evaluate whether gated prompt-projection is allowed for *policy*.

    Gate-off (default): always returns ``prompt_projection_allowed=False`` —
    byte-identical to today.

    Gate-on (``MAGI_MEMORY_PROJECTION_ENABLED=1``): returns
    ``prompt_projection_allowed=True`` ONLY when ``memory_mode`` is NOT
    ``"incognito"``.  Incognito always blocks projection regardless of the gate.

    The underlying :func:`evaluate_memory_policy` / :class:`MemoryPolicyDecision`
    remain unchanged; this function is the *gated tier* (D3 §1).
    """
    _ = request
    reasons: list[str] = []

    if not _projection_gate_open():
        reasons.append("projection_gate_off")
        return MemoryProjectionGateDecision(
            promptProjectionAllowed=False,
            reasonCodes=tuple(reasons),
        )

    if policy.memory_mode == "incognito":
        reasons.append("incognito_blocks_projection")
        return MemoryProjectionGateDecision(
            promptProjectionAllowed=False,
            reasonCodes=tuple(reasons),
        )

    reasons.append("projection_gate_on")
    return MemoryProjectionGateDecision(
        promptProjectionAllowed=True,
        reasonCodes=tuple(reasons),
    )
